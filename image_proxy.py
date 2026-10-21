"""Image-only proxy: explicit domains, public DNS addresses, pinned connections."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse
import httpx
from fastapi import HTTPException
from fastapi.responses import Response

REFERERS = {'tgb.cn': 'https://www.tgb.cn/', 'sinaimg.cn': 'https://weibo.com/',
            'sinajs.cn': 'https://weibo.com/',
            'sina.com.cn': 'https://weibo.com/', 'zsxq.com': 'https://wx.zsxq.com/',
            'qpic.cn': 'https://mp.weixin.qq.com/', 'qlogo.cn': 'https://weread.qq.com/',
            'weread.qq.com': 'https://weread.qq.com/', 'myqcloud.com': 'https://weread.qq.com/'}
MAX_SIZE = 12 * 1024 * 1024
MIME_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif', 'image/bmp'}

async def resolve_target(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        domain = next((d for d in REFERERS if host == d or host.endswith('.' + d)), None)
        if (parsed.scheme not in ('http', 'https') or not domain or parsed.username or
                parsed.password or parsed.port not in (None, 80, 443)):
            raise ValueError()
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or
                                             (443 if parsed.scheme == 'https' else 80), 0, socket.SOCK_STREAM)
        ips = [item[4][0] for item in addresses]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
        return host, ips[0], REFERERS[domain]
    except (ValueError, OSError):
        raise HTTPException(400, '不允许的图片地址')

async def fetch_image(url):
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            for attempt in range(5):
                host, ip, referer = await resolve_target(url)
                # Resolve once, connect to that exact public IP; preserve Host and TLS SNI.
                target = httpx.URL(url).copy_with(host=ip)
                headers = {'Host': host, 'Referer': referer, 'User-Agent': 'Mozilla/5.0',
                           'Accept-Encoding': 'identity'}
                async with client.stream('GET', target, headers=headers,
                                         extensions={'sni_hostname': host}) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, r.headers.get('location', ''))
                        continue
                    if r.status_code != 200:
                        raise HTTPException(502, '图片暂时无法读取')
                    mime = r.headers.get('content-type', '').split(';')[0].lower()
                    if mime not in MIME_TYPES:
                        raise HTTPException(415, '上游返回的不是支持的图片')
                    data = bytearray()
                    async for part in r.aiter_bytes():
                        data.extend(part)
                        if len(data) > MAX_SIZE:
                            raise HTTPException(413, '图片超过 12MB 限制')
                    return Response(bytes(data), media_type=mime,
                                    headers={'Cache-Control': 'private, max-age=86400',
                                             'X-Content-Type-Options': 'nosniff'})
            raise HTTPException(502, '图片跳转次数过多')
    except httpx.HTTPError:
        raise HTTPException(502, '图片网络请求失败')
