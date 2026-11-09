"""A local OpenAI-compatible provider with scripted answers, for CLI tests.

    python tests/fixtures/scripted_provider.py --port 8123 --mode solve

Modes:
  solve  one reply that solves MBPP task 11 in a single step
  prose  a reply with no code block, forever (exercises the feedback path)
  slow   sleeps, so a run can be interrupted while waiting on the provider
  down   always answers 500, to exercise retries and the error path
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ANSWER = (
    "def remove_Occ(s, ch):\n"
    "    first = s.find(ch)\n"
    "    if first != -1:\n"
    "        s = s[:first] + s[first + 1:]\n"
    "    last = s.rfind(ch)\n"
    "    if last != -1:\n"
    "        s = s[:last] + s[last + 1:]\n"
    "    return s"
)

SOLVE_REPLY = (
    "Thought: Write the function as a string, test it, submit it if the tests pass.\n"
    "```python\n"
    "import json\n"
    f"solution = {ANSWER!r}\n"
    "report = json.loads(run_tests(solution, test_list))\n"
    "print(report)\n"
    'if report["success"]:\n'
    "    final_answer(solution)\n"
    "```<end_code>"
)

PROSE_REPLY = "I would solve this with a loop, but I am not going to write any code right now."


def build_handler(mode: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if mode == "slow":
                time.sleep(30)
            if mode == "down":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"overloaded")
                return
            content = SOLVE_REPLY if mode == "solve" else PROSE_REPLY
            answer = {
                "model": body.get("model"),
                "choices": [
                    {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 620, "completion_tokens": 180},
            }
            payload = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mode", default="solve", choices=["solve", "prose", "slow", "down"])
    args = parser.parse_args()
    HTTPServer(("127.0.0.1", args.port), build_handler(args.mode)).serve_forever()


if __name__ == "__main__":
    main()
