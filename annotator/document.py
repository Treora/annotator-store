from annotator import es

TYPE = 'document'
MAPPING = {
    'annotator_schema_version': {'type': 'string'},
    'created': {'type': 'date'},
    'updated': {'type': 'date'},
    'title': {'type': 'string'},
    'link': {
        'type': 'nested',
        'properties': {
            'type': {'type': 'string', 'index': 'not_analyzed'},
            'href': {'type': 'string', 'index': 'not_analyzed'},
        }
    },
    'dc': {
        'type': 'nested',
        'properties': {
            # by default elastic search will try to parse this as
            # a date but unfortunately the data that is in the wild
            # may not be parsable by ES which throws an exception
            'date': {'type': 'string', 'index': 'not_analyzed'}
        }
    }
}


class Document(es.Model):
    __type__ = TYPE
    __mapping__ = MAPPING

    def uris(self):
        """Returns a list of the URIs for the document."""
        return self._uris_from_links(self.get('link', []))

    def merge_links(self, links):
        current_uris = self.uris()
        for l in links:
            if 'href' in l and 'type' in l and l['href'] not in current_uris:
                self['link'].append(l)

    def _uris_from_links(self, links):
        uris = []
        for link in links:
            uris.append(link.get('href'))
        return uris
