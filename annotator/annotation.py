import logging
log = logging.getLogger(__name__)
try:
    from collections import OrderedDict
except ImportError:
    try:
        from ordereddict import OrderedDict
    except ImportError:
        log.warn("No OrderedDict available, JSON-LD content will be unordered. "
                 "Use Python>=2.7 or install ordereddict module to fix.")
        OrderedDict = dict

from annotator import authz, document, es

TYPE = 'annotation'
MAPPING = {
    'id': {'type': 'string', 'index': 'no'},
    'annotator_schema_version': {'type': 'string'},
    'created': {'type': 'date'},
    'updated': {'type': 'date'},
    'quote': {'type': 'string', 'analyzer': 'standard'},
    'tags': {'type': 'string', 'index_name': 'tag'},
    'text': {'type': 'string', 'analyzer': 'standard'},
    'uri': {'type': 'string'},
    'user': {'type': 'string'},
    'consumer': {'type': 'string'},
    'ranges': {
        'index_name': 'range',
        'properties': {
            'start': {'type': 'string'},
            'end': {'type': 'string'},
            'startOffset': {'type': 'integer'},
            'endOffset': {'type': 'integer'},
        }
    },
    'permissions': {
        'index_name': 'permission',
        'properties': {
            'read': {'type': 'string'},
            'update': {'type': 'string'},
            'delete': {'type': 'string'},
            'admin': {'type': 'string'}
        }
    },
    'document': {
        'properties': document.MAPPING
    }
}


class Annotation(es.Model):

    __type__ = TYPE
    __mapping__ = MAPPING

    jsonld_baseurl = None

    def save(self, *args, **kwargs):
        _add_default_permissions(self)

        # If the annotation includes document metadata look to see if we have
        # the document modeled already. If we don't we'll create a new one
        # If we do then we'll merge the supplied links into it.

        if 'document' in self:
            d = self['document']
            uris = [link['href'] for link in d['link']]
            docs = document.Document.get_all_by_uris(uris)

            if len(docs) == 0:
                doc = document.Document(d)
                doc.save()
            else:
                doc = docs[0]
                links = d.get('link', [])
                doc.merge_links(links)
                doc.save()

        super(Annotation, self).save(*args, **kwargs)


    @property
    def jsonld(self):
        """The JSON-LD formatted RDF representation of the annotation."""

        context = [
            "http://www.w3.org/ns/oa-context-20130208.json",
            {'annotator': 'http://annotatorjs.org/ns/'}
        ]

        if self.jsonld_baseurl is not None:
            context.append({'@base': self.jsonld_baseurl})

        # The JSON-LD spec recommends to put @context at the top of the
        # document, so we'll be nice and use and ordered dictionary.
        annotation = OrderedDict()
        annotation['@context'] = context
        annotation['@id'] = self['id']
        annotation['@type'] = 'oa:Annotation'
        annotation['hasBody'] = self.has_body
        annotation['hasTarget'] = self.has_target
        annotation['annotatedBy'] = self.annotated_by
        annotation['annotatedAt'] = self.annotated_at
        annotation['serializedBy'] = self.serialized_by
        annotation['serializedAt'] = self.serialized_at
        annotation['motivatedBy'] = self.motivated_by
        return annotation

    @property
    def has_body(self):
        """Return all annotation bodies: the text comment and each tag"""
        bodies = []
        bodies += self.textual_bodies
        bodies += self.tags
        return bodies

    @property
    def textual_bodies(self):
        """A list with a single text body or an empty list"""
        if not self.get('text'):
            # Note that we treat an empty text as not having text at all.
            return []
        body = {
            '@type': ['dctypes:Text', 'cnt:ContentAsText'],
            'dc:format': 'text/plain',
            'cnt:chars': self['text'],
        }
        return [body]

    @property
    def tags(self):
        """A list of oa:Tag items"""
        if not 'tags' in self:
            return []
        return [
            {
                '@type': ['oa:Tag', 'cnt:ContentAsText'],
                'dc:format': 'text/plain',
                'cnt:chars': tag,
            }
            for tag in self['tags']
        ]

    @property
    def motivated_by(self):
        """Motivations for the annotation.

           Currently any combination of commenting and/or tagging.
        """
        motivations = []
        if self.textual_bodies:
            motivations.append('oa:commenting')
        if self.tags:
            motivations.append('oa:tagging')
        return motivations

    @property
    def has_target(self):
        """The targets of the annotation.

           Returns a selector for each range of the page content that was
           selected, or if a range is absent the url of the page itself.
        """
        targets = []
        if not 'uri' in self:
            return targets
        if self.get('ranges'):
            # Build the selector for each quote
            for rangeSelector in self['ranges']:
                selector = {
                    '@type': 'annotator:TextRangeSelector',
                    'annotator:startContainer': rangeSelector['start'],
                    'annotator:endContainer': rangeSelector['end'],
                    'annotator:startOffset': rangeSelector['startOffset'],
                    'annotator:endOffset': rangeSelector['endOffset'],
                }
                target = {
                    '@type': 'oa:SpecificResource',
                    'hasSource': self['uri'],
                    'hasSelector': selector,
                }
                targets.append(target)
        else:
            # The annotation targets the page as a whole
            targets.append(self['uri'])
        return targets

    @property
    def annotated_by(self):
        """The user that created the annotation."""
        if not self.get('user'):
            return []
        return {
            '@type': 'foaf:Agent', # It could be either a person or a bot
            'foaf:name': self['user'],
        }

    @property
    def annotated_at(self):
        """The annotation's creation date"""
        if self.get('created'):
            return self['created']

    @property
    def serialized_by(self):
        """The software used for serializing."""
        return {
            '@id': 'annotator:annotator-store',
            '@type': 'prov:Software-agent',
            'foaf:name': 'annotator-store',
            'foaf:homepage': {'@id': 'http://annotatorjs.org'},
        } # todo: add version number

    @property
    def serialized_at(self):
        """The last time the serialization changed."""
        # Following the spec[1], we do not use the current time, but the last
        # time the annotation graph has been updated.
        # [1]: https://hypothes.is/a/R6uHQyVTQYqBc4-1V9X56Q
        if self.get('updated'):
            return self['updated']


    @classmethod
    def search_raw(cls, query=None, params=None, user=None,
                   authorization_enabled=None, **kwargs):
        """Perform a raw Elasticsearch query

        Any ElasticsearchExceptions are to be caught by the caller.

        Keyword arguments:
        query -- Query to send to Elasticsearch
        params -- Extra keyword arguments to pass to Elasticsearch.search
        user -- The user to filter the results for according to permissions
        authorization_enabled -- Overrides Annotation.es.authorization_enabled
        raw_result -- Return Elasticsearch's response as is
        """
        if query is None:
            query = {}
        if authorization_enabled is None:
            authorization_enabled = es.authorization_enabled
        if authorization_enabled:
            f = authz.permissions_filter(user)
            if not f:
                raise RunTimeError("Authorization filter creation failed")
            filtered_query = {
                'filtered': {
                    'filter': f
                }
            }
            # Insert original query (if present)
            if 'query' in query:
                filtered_query['filtered']['query'] = query['query']
            # Use the filtered query instead of the original
            query['query'] = filtered_query

        res = super(Annotation, cls).search_raw(query=query,
                                                params=params,
                                                **kwargs)
        return res

    @classmethod
    def _build_query(cls, query=None, offset=None, limit=None,
                     user=None, **kwargs):
        if query is None:
            query = {}

        q = super(Annotation, cls)._build_query(query, offset, limit, **kwargs)

        # attempt to expand query to include uris for other representations
        # using information we may have on hand about the Document
        if 'uri' in query:
            term_filter = q['query']['filtered']['filter']
            doc = document.Document.get_by_uri(query['uri'])
            if doc:
                new_terms = []
                for term in term_filter['and']:
                    if 'uri' in term['term']:
                        term = {'or': []}
                        for uri in doc.uris():
                            term['or'].append({'term': {'uri': uri}})
                    new_terms.append(term)

                term_filter['and'] = new_terms

        if es.authorization_enabled:
            # Apply a filter to the results.
            f = authz.permissions_filter(user)
            if not f:
                return False  # Refuse to perform the query
            q['query'] = {'filtered': {'query': q['query'], 'filter': f}}

        return q


def _add_default_permissions(ann):
    if 'permissions' not in ann:
        ann['permissions'] = {'read': [authz.GROUP_CONSUMER]}
