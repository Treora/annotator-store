from annotator import authz, document, es

from flask import current_app, g

TYPE = 'annotation'
MAPPING = {
    'annotator_schema_version': {'type': 'string'},
    'created': {'type': 'date'},
    'updated': {'type': 'date'},
    'quote': {'type': 'string'},
    'tags': {'type': 'string', 'index_name': 'tag'},
    'text': {'type': 'string'},
    'uri': {'type': 'string', 'index': 'not_analyzed'},
    'user': {'type': 'string', 'index': 'not_analyzed'},
    'consumer': {'type': 'string', 'index': 'not_analyzed'},
    'ranges': {
        'index_name': 'range',
        'properties': {
            'start': {'type': 'string', 'index': 'not_analyzed'},
            'end': {'type': 'string', 'index': 'not_analyzed'},
            'startOffset': {'type': 'integer'},
            'endOffset': {'type': 'integer'},
        }
    },
    'permissions': {
        'index_name': 'permission',
        'properties': {
            'read': {'type': 'string', 'index': 'not_analyzed'},
            'update': {'type': 'string', 'index': 'not_analyzed'},
            'delete': {'type': 'string', 'index': 'not_analyzed'},
            'admin': {'type': 'string', 'index': 'not_analyzed'}
        }
    },
    'document': {
        'properties': document.MAPPING
    }
}


class Annotation(es.Model):

    __type__ = TYPE
    __mapping__ = MAPPING

    def save(self, *args, **kwargs):
        _add_default_permissions(self)

        # If the annotation includes document metadata look to see if we have
        # the document modeled already. If we don't we'll create a new one
        # If we do then we'll merge the supplied links into it.

        if 'document' in self:
            d = self['document']
            uri = [link['href'] for link in d['link']]
            q = {'terms': {'link.href': uri}}
            docs = document.Document.search(link=q)

            if len(docs) == 0:
                doc = document.Document(d)
                doc.save()
            else:
                doc = docs[0]
                links = d.get('link', [])
                doc.merge_links(links)
                doc.save()

        super(Annotation, self).save(*args, **kwargs)

    @classmethod
    def _build_query(cls, offset=0, limit=20, **kwargs):
        q = super(Annotation, cls)._build_query(offset, limit, **kwargs)

        # attempt to expand query to include uris for other representations
        # using information we may have on hand about the Document
        if 'uri' in kwargs:
            uri = kwargs['uri']
            q = cls._expand_documents(q, uri)

        if current_app.config.get('AUTHZ_ON'):
            f = authz.permissions_filter(g.user)
            if not f:
                return False  # Refuse to perform the query
            q['query'] = {'filtered': {'query': q['query'], 'filter': f}}

        return q

    @classmethod
    def _build_query_raw(cls, request):
        q, p = super(Annotation, cls)._build_query_raw(request)

        if current_app.config.get('AUTHZ_ON'):
            f = authz.permissions_filter(g.user)
            if not f:
                return {'error': 'Authorization error!', 'status': 400}, None
            q['query'] = {'filtered': {'query': q['query'], 'filter': f}}

        return q, p

    @classmethod
    def _expand_documents(cls, q, uri):
        if isinstance(uri, basestring): uri = [uri]
        qd = {'terms': {'link.href': uri}}
        docs = document.Document.search(link=qd)
        if not docs: return q

        for d in docs:
            for u in d.uris():
                if not u in uri:
                    uri.append(u)

        clauses = q['query']['filtered']['filter']['and']
        for clause in clauses:
            if 'term' in clause and 'uri' in clause['term']:
                clause['terms'] = {'uri': uri}
                del clause['term']['uri']
                if not clause['term']: del clause['term']
                break

            if 'terms' in clause and 'uri' in clause['terms']:
                clause['terms']['uri'] = uri
                break

        return q


def _add_default_permissions(ann):
    if 'permissions' not in ann:
        ann['permissions'] = {'read': [authz.GROUP_CONSUMER]}
