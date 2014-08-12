"""
This module implements a Flask-based JSON API to talk with the annotation store
via the Annotation model.
It defines these routes:
  * Root
  * Index (OA)
  * Create
  * Read (OA)
  * Update
  * Delete
  * Search (OA)
  * Raw ElasticSearch search
See their descriptions in `root`'s definition for more detail.

Routes marked with OA (the read-only endpoints) will render the annotations in
JSON-LD following the Open Annotation Data Model if the user agent prefers this
(by accepting application/ld+json).
"""
from __future__ import absolute_import

import json

from elasticsearch.exceptions import TransportError
from flask import Blueprint, Response
from flask import current_app, g
from flask import request
from flask import url_for
from six import iteritems

from annotator.atoi import atoi
from annotator.annotation import Annotation
from annotator.openannotation import OAAnnotation

store = Blueprint('store', __name__)

CREATE_FILTER_FIELDS = ('updated', 'created', 'consumer', 'id')
UPDATE_FILTER_FIELDS = ('updated', 'created', 'user', 'consumer')


# We define our own jsonify rather than using flask.jsonify because we wish
# to jsonify arbitrary objects (e.g. index returns a list) rather than kwargs.
def jsonify(obj, *args, **kwargs):
    res = json.dumps(obj, indent=None if request.is_xhr else 2)
    return Response(res, mimetype='application/json', *args, **kwargs)


"""
Define renderers that can be used for presenting the annotation. Note that we
currently only use JSON-based types. The renderer returns not a string but a
jsonifiable object.
"""
def render_jsonld(annotation):
    """Returns a JSON-LD RDF representation of the annotation"""
    oa_annotation = OAAnnotation(annotation)
    oa_annotation.jsonld_baseurl = url_for('.read_annotation',
                                           id='', _external=True)
    return oa_annotation.jsonld

renderers = {
    'application/ld+json': render_jsonld,
    'application/json': lambda annotation: annotation,
}
types_by_preference = ['application/json', 'application/ld+json']

def render(annotation, content_type=None):
    """Return the annotation in the given or negotiated content_type"""
    if content_type is None:
        content_type = preferred_content_type(types_by_preference)
    return renderers[content_type](annotation)


@store.before_request
def before_request():
    if not hasattr(g, 'annotation_class'):
        g.annotation_class = Annotation

    user = g.auth.request_user(request)
    if user is not None:
        g.user = user
    elif not hasattr(g, 'user'):
        g.user = None


@store.after_request
def after_request(response):
    ac = 'Access-Control-'
    rh = response.headers

    rh[ac + 'Allow-Origin'] = request.headers.get('origin', '*')
    rh[ac + 'Expose-Headers'] = 'Content-Length, Content-Type, Location'

    if request.method == 'OPTIONS':
        rh[ac + 'Allow-Headers'] = ('Content-Length, Content-Type, '
                                    'X-Annotator-Auth-Token, X-Requested-With')
        rh[ac + 'Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        rh[ac + 'Max-Age'] = '86400'

    return response


# ROOT
@store.route('/')
def root():
    return jsonify({
        'message': "Annotator Store API",
        'links': {
            'annotation': {
                'create': {
                    'method': 'POST',
                    'url': url_for('.create_annotation', _external=True),
                    'query': {
                        'refresh': {
                            'type': 'bool',
                            'desc': ("Force an index refresh after create "
                                     "(default: true)")
                        }
                    },
                    'desc': "Create a new annotation"
                },
                'read': {
                    'method': 'GET',
                    'url': url_for('.read_annotation',
                                   id=':id',
                                   _external=True),
                    'desc': "Get an existing annotation"
                },
                'update': {
                    'method': 'PUT',
                    'url':
                    url_for(
                        '.update_annotation',
                        id=':id',
                        _external=True),
                    'query': {
                        'refresh': {
                            'type': 'bool',
                            'desc': ("Force an index refresh after update "
                                     "(default: true)")
                        }
                    },
                    'desc': "Update an existing annotation"
                },
                'delete': {
                    'method': 'DELETE',
                    'url': url_for('.delete_annotation',
                                   id=':id',
                                   _external=True),
                    'desc': "Delete an annotation"
                }
            },
            'search': {
                'method': 'GET',
                'url': url_for('.search_annotations', _external=True),
                'desc': 'Basic search API'
            },
            'search_raw': {
                'method': 'GET/POST',
                'url': url_for('.search_annotations_raw', _external=True),
                'desc': ('Advanced search API -- direct access to '
                         'ElasticSearch. Uses the same API as the '
                         'ElasticSearch query endpoint.')
            }
        }
    })


# INDEX
@store.route('/annotations')
def index():
    if current_app.config.get('AUTHZ_ON'):
        # Pass the current user to do permission filtering on results
        user = g.user
    else:
        user = None

    annotations = g.annotation_class.search(user=user)

    return jsonify(list(map(render, annotations)))


# CREATE
@store.route('/annotations', methods=['POST'])
def create_annotation():
    # Only registered users can create annotations
    if g.user is None:
        return _failed_authz_response('create annotation')

    if request.json is not None:
        annotation = g.annotation_class(
            _filter_input(
                request.json,
                CREATE_FILTER_FIELDS))

        annotation['consumer'] = g.user.consumer.key
        if _get_annotation_user(annotation) != g.user.id:
            annotation['user'] = g.user.id

        if hasattr(g, 'before_annotation_create'):
            g.before_annotation_create(annotation)

        if hasattr(g, 'after_annotation_create'):
            annotation.save(refresh=False)
            g.after_annotation_create(annotation)

        refresh = request.args.get('refresh') != 'false'
        annotation.save(refresh=refresh)

        return jsonify(annotation)
    else:
        return jsonify('No JSON payload sent. Annotation not created.',
                       status=400)


# READ
@store.route('/annotations/<id>')
def read_annotation(id):
    annotation = g.annotation_class.fetch(id)
    if not annotation:
        return jsonify('Annotation not found!', status=404)

    failure = _check_action(annotation, 'read')
    if failure:
        return failure


    return jsonify(render(annotation))


# UPDATE
@store.route('/annotations/<id>', methods=['POST', 'PUT'])
def update_annotation(id):
    annotation = g.annotation_class.fetch(id)
    if not annotation:
        return jsonify('Annotation not found! No update performed.',
                       status=404)

    failure = _check_action(annotation, 'update')
    if failure:
        return failure

    if request.json is not None:
        updated = _filter_input(request.json, UPDATE_FILTER_FIELDS)
        updated['id'] = id  # use id from URL, regardless of what arrives in
                            # JSON payload

        changing_permissions = (
            'permissions' in updated and
            updated['permissions'] != annotation.get('permissions', {}))

        if changing_permissions:
            failure = _check_action(annotation,
                                    'admin',
                                    message='permissions update')
            if failure:
                return failure

        annotation.update(updated)

        if hasattr(g, 'before_annotation_update'):
            g.before_annotation_update(annotation)

        refresh = request.args.get('refresh') != 'false'
        annotation.save(refresh=refresh)

        if hasattr(g, 'after_annotation_update'):
            g.after_annotation_update(annotation)

    return jsonify(annotation)


# DELETE
@store.route('/annotations/<id>', methods=['DELETE'])
def delete_annotation(id):
    annotation = g.annotation_class.fetch(id)

    if not annotation:
        return jsonify('Annotation not found. No delete performed.',
                       status=404)

    failure = _check_action(annotation, 'delete')
    if failure:
        return failure

    if hasattr(g, 'before_annotation_delete'):
        g.before_annotation_delete(annotation)

    annotation.delete()

    if hasattr(g, 'after_annotation_delete'):
        g.after_annotation_delete(annotation)

    return '', 204


# SEARCH
@store.route('/search')
def search_annotations():
    params = dict(request.args.items())
    kwargs = dict()

    # Take limit and offset out of the parameters
    if 'offset' in params:
        kwargs['offset'] = atoi(params.pop('offset'), default=None)
    if 'limit' in params:
        kwargs['limit'] = atoi(params.pop('limit'), default=None)

    # All remaining parameters are considered searched fields.
    kwargs['query'] = params

    if current_app.config.get('AUTHZ_ON'):
        # Pass the current user to do permission filtering on results
        kwargs['user'] = g.user

    results = g.annotation_class.search(**kwargs)
    total = g.annotation_class.count(**kwargs)

    return jsonify({'total': total,
                    'rows': list(map(render, results))})


# RAW ES SEARCH
@store.route('/search_raw', methods=['GET', 'POST'])
def search_annotations_raw():

    try:
        query, params = _build_query_raw(request)
    except ValueError:
        return jsonify('Could not parse request payload!',
                       status=400)

    kwargs = dict()
    if current_app.config.get('AUTHZ_ON'):
        kwargs['user'] = g.user

    try:
        res = g.annotation_class.search_raw(query, params, raw_result=True,
                                            **kwargs)
    except TransportError as err:
        if err.status_code is not 'N/A':
            status_code = err.status_code
        else:
            status_code = 500
        return jsonify(err.error,
                       status=status_code)
    return jsonify(res, status=res.get('status', 200))


def _filter_input(obj, fields):
    for field in fields:
        obj.pop(field, None)

    return obj


def _get_annotation_user(ann):
    """Returns the best guess at this annotation's owner user id"""
    user = ann.get('user')

    if not user:
        return None

    try:
        return user.get('id', None)
    except AttributeError:
        return user


def _check_action(annotation, action, message=''):
    if not g.authorize(annotation, action, g.user):
        return _failed_authz_response(message)


def _failed_authz_response(msg=''):
    user = g.user.id if g.user else None
    consumer = g.user.consumer.key if g.user else None
    return jsonify("Cannot authorize request{0}. Perhaps you're not logged in "
                   "as a user with appropriate permissions on this "
                   "annotation? "
                   "(user={user}, consumer={consumer})".format(
                       ' (' + msg + ')' if msg else '',
                       user=user,
                       consumer=consumer),
                   status=401)


def _build_query_raw(request):
    query = {}
    params = {}

    if request.method == 'GET':
        for k, v in iteritems(request.args):
            _update_query_raw(query, params, k, v)

        if 'query' not in query:
            query['query'] = {'match_all': {}}

    elif request.method == 'POST':

        try:
            query = json.loads(request.json or
                               request.data or
                               request.form.keys()[0])
        except (ValueError, IndexError):
            raise ValueError

        params = request.args

    for o in (params, query):
        if 'from' in o:
            o['from'] = max(0, atoi(o['from']))
        if 'size' in o:
            o['size'] = min(RESULTS_MAX_SIZE, max(0, atoi(o['size'])))

    return query, params


def _update_query_raw(qo, params, k, v):
    if 'query' not in qo:
        qo['query'] = {}
    q = qo['query']

    if 'query_string' not in q:
        q['query_string'] = {}
    qs = q['query_string']

    if k == 'q':
        qs['query'] = v

    elif k == 'df':
        qs['default_field'] = v

    elif k in ('explain', 'track_scores', 'from', 'size', 'timeout',
               'lowercase_expanded_terms', 'analyze_wildcard'):
        qo[k] = v

    elif k == 'fields':
        qo[k] = _csv_split(v)

    elif k == 'sort':
        if 'sort' not in qo:
            qo[k] = []

        split = _csv_split(v, ':')

        if len(split) == 1:
            qo[k].append(split[0])
        else:
            fld = ':'.join(split[0:-1])
            drn = split[-1]
            qo[k].append({fld: drn})

    elif k == 'search_type':
        params[k] = v

def preferred_content_type(possible_types):
    """Tells which content (MIME) type is preferred by the user agent.

       In case of ties (or absence of an Accept header) items earlier in the
       sequence are chosen.

       Arguments:
       possible_types -- Sequence of content types, in order of preference.
    """
    default = possible_types[0]
    best_type = request.accept_mimetypes.best_match(
        possible_types,
        default)
    return best_type
