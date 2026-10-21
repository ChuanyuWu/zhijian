"""One HTML policy for newly fetched and historical posts."""
import re
from urllib.parse import urljoin, urlparse, urlencode
import bleach
from lxml import html as lh

TAGS = {'p', 'div', 'span', 'br', 'a', 'strong', 'b', 'em', 'i', 'u', 's',
        'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'pre', 'code',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'img', 'sup', 'sub'}

def safe_url(value, base=''):
    try:
        value = urljoin(base, value or '').strip()
        u = urlparse(value)
        if u.scheme in ('http', 'https') and u.hostname and not u.username and not u.password:
            return value
    except (ValueError, TypeError):
        pass
    return ''

def sanitize_html(raw, base='https://m.weibo.cn/'):
    clean = bleach.clean(raw or '', tags=TAGS,
                         attributes={'a': ['href'], 'img': ['src', 'alt'], 'div': ['class']},
                         protocols={'http', 'https'}, strip=True, strip_comments=True)
    tree = lh.fragment_fromstring(clean, create_parent='div')
    for node in tree.iter('a'):
        href = safe_url(node.get('href'), base) if node.get('href') else ''
        node.attrib.clear()
        if href:
            node.set('href', href)
            node.set('target', '_blank')
            node.set('rel', 'noopener noreferrer')
    for node in list(tree.iter('img')):
        src = node.get('src', '')
        if src.startswith('/api/img?'):
            node.set('loading', 'lazy')
            continue
        src = safe_url(src, base)
        node.attrib.clear()
        if src:
            node.set('src', '/api/img?' + urlencode({'url': src}))
            node.set('loading', 'lazy')
        else:
            node.drop_tree()
    wrapped = lh.tostring(tree, encoding='unicode', method='html')
    return wrapped[5:-6]

def needs_fulltext(raw):
    tree = lh.fragment_fromstring(raw or '', create_parent='div')
    return any('全文' in ''.join(a.itertext()) and
               re.search(r'/(?:status|detail)/', a.get('href', '')) for a in tree.iter('a'))

def present_post(post):
    p = dict(post)
    p['url'] = safe_url(p.get('url', ''))
    if p.get('platform') == 'weibo' and str(p.get('post_id', '')).isdigit():
        p['url'] = 'https://m.weibo.cn/detail/' + str(p['post_id'])
    p['content_html'] = sanitize_html(p.get('content_html', ''), p['url'] or 'https://m.weibo.cn/')
    p['images'] = [url for url in (safe_url(x) for x in p.get('images', [])) if url]
    if p.get('platform') == 'weibo':
        p['needs_fulltext'] = bool(p.get('needs_fulltext')) or needs_fulltext(p['content_html'])
    return p
