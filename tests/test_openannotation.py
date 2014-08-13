import re

from annotator.annotation import Annotation
from annotator.openannotation import OAAnnotation
from annotator.elasticsearch import _add_created, _add_updated

class TestOpenAnnotation(object):

    def _make_annotation(self):
        annotation_fields = {
            'id': '1234',
            'text': 'blablabla',
            'uri': 'http://localhost:4000/dev.html',
            'ranges': [
                {
                'start': '/ul[1]/li[1]',
                'end': '/ul[1]/li[1]',
                'startOffset': 0,
                'endOffset': 26
                }
            ],
            'user': 'alice',
            'quote': 'Lorem ipsum dolor sit amet',
            'consumer': 'mockconsumer',
            'permissions': {
                'read': [],
                'admin': [],
                'update': [],
                'delete': []
            }
        }
        annotation = OAAnnotation(annotation_fields)
        _add_created(annotation)
        _add_updated(annotation)
        return annotation

    def test_basics(self):
        ann = self._make_annotation()

        # Get the JSON-LD (as a dictionary)
        ann_ld = ann.jsonld

        # Check the values of some basic fields
        ldid = ann_ld['@id']
        assert ldid == '1234', "Incorrect annotation @id: {0}!={1}".format(ldid, id)
        assert ann_ld['@type'] == 'oa:Annotation'
        assert ann_ld['hasBody'] == [{
            "cnt:chars": "blablabla",
            "@type": [
                "dctypes:Text",
                "cnt:ContentAsText"
            ],
            "dc:format": "text/plain"
        }], "Incorrect hasBody: {0}".format(ann_ld['hasBody'])

        assert ann_ld['hasTarget'] == [{
            "hasSource": "http://localhost:4000/dev.html",
            "hasSelector": {
                "annotator:endContainer": "/ul[1]/li[1]",
                "annotator:startOffset": 0,
                "annotator:startContainer": "/ul[1]/li[1]",
                "@type": "annotator:TextRangeSelector",
                "annotator:endOffset": 26
            },
            "@type": "oa:SpecificResource"
        }], "Incorrect hasTarget: {0}".format(ann_ld['hasBody'])

        assert ann_ld['annotatedBy'] == {
            '@type': 'foaf:Agent',
            'foaf:name': 'alice',
        }, "Incorrect annotatedBy: {0}".format(ann_ld['annotatedBy'])

        date_str = "nnnn-nn-nnTnn:nn:nn(\.nnnnnn)?([+-]nn.nn|Z)"
        date_regex = re.compile(date_str.replace("n","\d"))
        assert date_regex.match(ann_ld['annotatedAt']), "Incorrect annotatedAt: {0}".format(ann_ld['annotatedAt'])
        assert date_regex.match(ann_ld['serializedAt']), "Incorrect createdAt: {0}".format(ann_ld['annotatedAt'])


def assemble_context(context_value):
    if isinstance(context_value, dict):
        return context_value
    elif isinstance(context_value, list):
        # Merge all context parts
        context = {}
        for context_piece in context_value:
            if isinstance(context_piece, dict):
                context.update(context_piece)
        return context
    elif isinstance(context, str):
        # XXX: we do not retrieve an externally defined context
        raise NotImplementedError
    else:
        raise AssertionError("@context should be dict, list, or str")
