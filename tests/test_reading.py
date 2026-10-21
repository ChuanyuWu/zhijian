import asyncio
import unittest
from unittest.mock import patch
import test_core
from content_safety import present_post, sanitize_html
from fulltext import Prefetcher

class ReadingTests(unittest.TestCase):
    def test_update_stream_is_named_and_unsubscribes(self):
        import main
        import monitor
        async def run():
            before=len(monitor._subscribers)
            response=await main.stream()
            monitor._broadcast({'event':'post_updated','post':{'id':123}})
            event=await anext(response.body_iterator)
            self.assertTrue(event.startswith('event: post_updated\n'))
            await response.body_iterator.aclose()
            self.assertEqual(len(monitor._subscribers),before)
        asyncio.run(run())

    def test_emoji_alt_and_single_summary_entry(self):
        raw='<img src="https://face.t.sinajs.cn/a.png" alt="[偷笑]" onerror="bad()"><a href="/status/123">全文</a>'
        clean=sanitize_html(raw)
        self.assertIn('alt="[偷笑]"',clean)
        self.assertNotIn('onerror',clean)
        post=present_post(dict(platform='weibo',post_id='123',content_html=raw))
        self.assertTrue(post['needs_fulltext'])
        self.assertNotIn('>全文</a>',post['content_html'])
        self.assertTrue(present_post(post)['needs_fulltext'])

    def test_prefetch_dedup_and_update_event(self):
        async def run():
            worker=Prefetcher()
            p={'id':123,'platform':'weibo','needs_fulltext':True}
            worker.enqueue([p,p,{'id':456,'platform':'taoguba','needs_fulltext':True}])
            self.assertEqual(worker.queue.qsize(),1)
            with patch('fulltext.complete_post',return_value=p|{'needs_fulltext':False}),patch('monitor._broadcast') as broadcast:
                task=asyncio.create_task(worker.run())
                await asyncio.wait_for(worker.queue.join(),3)
                self.assertEqual(broadcast.call_args.args[0]['event'],'post_updated')
                self.assertFalse(broadcast.call_args.args[0]['post']['needs_fulltext'])
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
        asyncio.run(run())

if __name__=='__main__':unittest.main()
