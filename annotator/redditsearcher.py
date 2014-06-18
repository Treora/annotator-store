"""
Module to search for links on reddit about a given URL, and return them in annotator format.
"""
version = 0.1

import requests
import time
import sys
import cgi

api_domain = "api.reddit.com"
api_url = "http://" + api_domain

def search_raw(query, limit=10):
    """Query Reddit's API to search for links that target the given URL.
    Reddit's search API is described here: See http://www.reddit.com/dev/api#GET_search
    """
    endpoint_url = api_url + "/search.json"
    params = {
        'q': query,
        'limit': limit,
        'sort': 'relevance',
        'syntax': 'lucene',
        't': 'all',
        'restrict_sr': False,
    }
    headers = {
        'Content-Type': 'application/json',
        'User-agent': 'Hypothes.is Reddit-searcher test v%s' % version,
        'Host': api_domain,
    }
    response = requests.get(endpoint_url, params=params, headers=headers)
    response_data = response.json()
    try:
        result_list = response_data['data']['children']
    except TypeError:
        from pprint import pprint
        pprint(response_data)
        return []
    return result_list

def search(uri=None, limit=10, query=None):
    if not query:
        query = uri_to_query(uri)
    result_list = search_raw(query, limit)
    return map(reddit_link_to_annotation, result_list)

def uri_to_query(uri):
    return 'url:"%s"' % uri

def reddit_link_to_annotation(link):
    """Given a link object from the reddit api create an annotation in annotator's format."""
    try:
        data = link['data']
        creation_date = time.gmtime(float(data['created_utc']))
        # Date should look like this: "2014-06-18T02:01:05.435649+00:00",
        creation_date_string = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", creation_date)
        
        cleaned_title = cgi.escape(data['title'])
        # Front-end wraps the text in <p>...</p>, so we can insert '</p><p>' to create two paragraphs.
        annotation_content = u"""
{cleaned_title}
</p><p>
<a href="http://reddit.com{permalink}">
    <em>
        Read {num_comments} reactions in Reddit/{subreddit}
    </em>
</a>
""".format(cleaned_title = cleaned_title,
           permalink = data['permalink'],
           num_comments = data['num_comments'],
           subreddit = data['subreddit'])
        
        annotation = {
            'id': 'reddit-t3_%s' % data['id'],
            "updated": creation_date_string,
            'target': [],
            "created": creation_date_string, 
            'quote': '',
            'uri': data['url'],
            'user': 'acct:%s@reddit.com' % data['author'],
            'text': annotation_content,
            "permissions": {
            "read": [
                "group:__world__"
            ], 
            "admin": [
            ], 
            "update": [
            ], 
            "delete": [
            ]
            },
            "reply_list": [],
            "document": {},
        }
        return annotation
    except Exception, e:
        return {'error': 'ERROR in converting reddit data to annotation: %s ' % e}

def main():
    if len(sys.argv) > 1:
        uri = sys.argv[1]
    else:
        sys.exit("Please give a URI to search for.")
    query = uri_to_query(uri)
    result_list = search_raw(query)
    for result in result_list:
        print summarise_link(result)

def summarise_link(reddit_link):
    data = reddit_link['data']
    title = data['title']
    if len(title) > 80:
        title = title[:80-5] + "....."
    string = u"""{title}
by {data[author]}, {data[num_comments]} comments.
target: {data[url]}, score: {data[score]}
permalink: http://reddit.com{data[permalink]}
""".format(data=data, title=title)
    return string


if __name__ == "__main__":
    main()
