"""Smoke test: start pipelines/paper2result.pipe on the local RocketRide
engine and ask the agent a graph question.

Usage: python scripts/check_pipeline.py ["question"]
"""

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from rocketride import RocketRideClient
from rocketride.schema import Question

PIPE = os.path.join(ROOT, "pipelines", "paper2result.pipe")
DEFAULT_QUESTION = "Which claims currently have executable evidence attached, and what did the runs show?"


async def _pipeline_token(client: RocketRideClient) -> str:
    """Start the pipeline or attach to an already-running instance."""
    try:
        result = await client.use(filepath=PIPE)
        return result["token"]
    except RuntimeError as e:
        if "already running" not in str(e).lower():
            raise
        with open(PIPE) as f:
            project_id = json.load(f)["project_id"]
        token = await client.get_task_token(project_id, "chat_1")
        if not token:
            raise RuntimeError("pipeline is running but no chat_1 task token found")
        return token


async def main() -> int:
    question_text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    client = RocketRideClient()
    await client.connect()
    print("connected to RocketRide engine")
    try:
        token = await _pipeline_token(client)
        print(f"pipeline ready, token={token}")

        q = Question()
        q.addQuestion(question_text)
        print(f"Q: {question_text}")
        response = await client.chat(token=token, question=q)

        answers = response.get("answers", [])
        if not answers:
            # laneName may map differently; consult result_types
            for key, lane in response.get("result_types", {}).items():
                if lane == "answers":
                    answers = response.get(key, [])
                    break
        if not answers:
            print(f"NO ANSWER. raw response keys: {list(response)}")
            return 1
        print("A:", answers[0])
        return 0
    finally:
        await client.disconnect()
        print("disconnected")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
