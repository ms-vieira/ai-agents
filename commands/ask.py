"""Read questions from the terminal and call the orchestrator that is already up."""

import httpx

URL = "http://127.0.0.1:8500/v1/perguntar"


def main() -> None:
    print("Ask the orchestrator. An empty line or Ctrl+D exits.")
    while True:
        try:
            question = input("question> ")
        except EOFError:
            print()
            return
        if not question.strip():
            return
        try:
            response = httpx.post(URL, json={"question": question}, timeout=30)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"the orchestrator did not respond: {exc}")
            print("check that the other terminal still shows: up at http://127.0.0.1:8500")
            print()
            continue
        body = response.json()
        answer = body.get("answer") or body["parecer"]["answer"]
        print(answer)
        print()


if __name__ == "__main__":
    main()
