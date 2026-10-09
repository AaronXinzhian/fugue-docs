#!/usr/bin/env python3
"""
[INPUT]: 依赖 http.server, json, threading
[OUTPUT]: 提供本地 Messages API 替身 MockMessagesAPI:按脚本依次返回工具调用,最后返回结束文本(流式 SSE 与非流式)
[POS]: fugue-docs 评测包-Claude Code 零成本自检的假服务端;只监听 127.0.0.1,不转发任何请求
[PROTOCOL]: 改事件格式或脚本语义时同步 run_token_pilot.claude_selftest 与 test_token_pilot.py
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class MockMessagesAPI:
    """脚本化的假 Messages API。

    steps 是 [{"name": 工具名, "input": {...}}, ...]:请求里已有 k 个 tool_result 时返回第 k 步的工具调用,
    步骤用完后返回一段结束文本。用量是固定的小数字,只为让 CLI 走完正常的计量路径。
    """

    def __init__(self, steps, final_text="Self-test finished."):
        self.steps, self.final_text = list(steps), final_text
        self.requests = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply_json(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "mock"}})

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                api.requests.append({"path": self.path, "model": body.get("model"), "stream": body.get("stream")})
                if "count_tokens" in self.path:
                    self.reply_json(200, {"input_tokens": 100})
                    return
                if not self.path.startswith("/v1/messages"):
                    self.reply_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "mock"}})
                    return
                api.answer(self, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def next_block(self, body):
        done = sum(1 for message in body.get("messages") or [] if isinstance(message.get("content"), list)
                   for part in message["content"] if isinstance(part, dict) and part.get("type") == "tool_result")
        tools = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
        if done < len(self.steps) and self.steps[done]["name"] in tools:
            step = self.steps[done]
            return ({"type": "tool_use", "id": "toolu_selftest_%d" % done, "name": step["name"], "input": {}},
                    {"type": "input_json_delta", "partial_json": json.dumps(step["input"])}, "tool_use")
        return ({"type": "text", "text": ""}, {"type": "text_delta", "text": self.final_text}, "end_turn")

    def answer(self, handler, body):
        usage = {"input_tokens": 100, "output_tokens": 1, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        message_id = "msg_selftest_%d" % len(self.requests)
        if not body.get("stream"):
            handler.reply_json(200, {"id": message_id, "type": "message", "role": "assistant",
                                     "model": body.get("model"), "stop_reason": "end_turn", "stop_sequence": None,
                                     "content": [{"type": "text", "text": "Self-test"}], "usage": usage})
            return
        block, delta, stop = self.next_block(body)
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.end_headers()
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": message_id, "type": "message", "role": "assistant", "model": body.get("model"),
                "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 10}}),
            ("message_stop", {"type": "message_stop"})]
        for name, data in events:
            handler.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode())
            handler.wfile.flush()
