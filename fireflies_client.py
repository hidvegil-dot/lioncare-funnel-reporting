from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv


FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"


class FirefliesAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FirefliesConfig:
    api_key: str
    graphql_url: str = FIREFLIES_GRAPHQL_URL
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "FirefliesConfig":
        api_key = os.getenv("FIREFLIES_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Missing FIREFLIES_API_KEY environment variable")
        graphql_url = os.getenv("FIREFLIES_GRAPHQL_URL", cls.graphql_url).strip() or cls.graphql_url
        return cls(api_key=api_key, graphql_url=graphql_url)


class FirefliesClient:
    def __init__(self, config: FirefliesConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.post(
            self.config.graphql_url,
            json={"query": query, "variables": variables or {}},
            timeout=self.config.request_timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise FirefliesAPIError(
                f"Fireflies API returned non-JSON response status={response.status_code}"
            ) from exc
        if response.status_code >= 400:
            raise FirefliesAPIError(
                f"Fireflies API request failed status={response.status_code}: {payload}"
            )
        if payload.get("errors"):
            raise FirefliesAPIError(f"Fireflies GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}

    def list_transcripts(
        self,
        *,
        limit: int = 10,
        skip: int = 0,
        from_date: str | None = None,
        to_date: str | None = None,
        mine: bool | None = None,
    ) -> list[dict[str, Any]]:
        query = """
        query Transcripts($limit: Int, $skip: Int, $fromDate: DateTime, $toDate: DateTime, $mine: Boolean) {
          transcripts(limit: $limit, skip: $skip, fromDate: $fromDate, toDate: $toDate, mine: $mine) {
            id
            title
            date
            duration
            organizer_email
            participants
            transcript_url
            summary {
              short_summary
              overview
              action_items
            }
          }
        }
        """
        variables = {
            "limit": max(1, min(limit, 50)),
            "skip": max(0, skip),
            "fromDate": from_date,
            "toDate": to_date,
            "mine": mine,
        }
        data = self.graphql(query, variables)
        return data.get("transcripts") or []

    def get_transcript(self, transcript_id: str, *, include_sentences: bool = True) -> dict[str, Any]:
        if include_sentences:
            query = """
            query Transcript($transcriptId: String!) {
              transcript(id: $transcriptId) {
                id
                title
                date
                duration
                organizer_email
                participants
                transcript_url
                summary {
                  keywords
                  action_items
                  outline
                  overview
                  bullet_gist
                  short_summary
                  topics_discussed
                }
                sentences {
                  index
                  speaker_name
                  text
                  start_time
                  end_time
                }
              }
            }
            """
        else:
            query = """
            query Transcript($transcriptId: String!) {
              transcript(id: $transcriptId) {
                id
                title
                date
                duration
                organizer_email
                participants
                transcript_url
                summary {
                  keywords
                  action_items
                  outline
                  overview
                  bullet_gist
                  short_summary
                  topics_discussed
                }
              }
            }
            """
        data = self.graphql(query, {"transcriptId": transcript_id})
        return data.get("transcript") or {}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Fireflies API smoke test and transcript fetch helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent Fireflies transcripts.")
    list_parser.add_argument("--limit", type=int, default=5)
    list_parser.add_argument("--skip", type=int, default=0)
    list_parser.add_argument("--from-date", help="ISO datetime, e.g. 2026-06-01T00:00:00.000Z")
    list_parser.add_argument("--to-date", help="ISO datetime, e.g. 2026-06-08T00:00:00.000Z")
    list_parser.add_argument("--mine", action="store_true", help="Only meetings owned by the API key user.")

    get_parser = subparsers.add_parser("get", help="Fetch one transcript by ID.")
    get_parser.add_argument("transcript_id")
    get_parser.add_argument("--no-sentences", action="store_true", help="Fetch metadata and summary only.")

    args = parser.parse_args()
    client = FirefliesClient(FirefliesConfig.from_env())
    if args.command == "list":
        transcripts = client.list_transcripts(
            limit=args.limit,
            skip=args.skip,
            from_date=args.from_date,
            to_date=args.to_date,
            mine=True if args.mine else None,
        )
        print(json.dumps(transcripts, ensure_ascii=False, indent=2))
    elif args.command == "get":
        transcript = client.get_transcript(
            args.transcript_id,
            include_sentences=not args.no_sentences,
        )
        print(json.dumps(transcript, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
