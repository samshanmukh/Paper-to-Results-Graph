"""Smoke test: verify Neo4j Aura connectivity using credentials from .env."""

import os
import sys

import certifi

# macOS python.org builds ship without system CA certs; the Aura TLS handshake
# fails unless the SSL context points at certifi's bundle before connecting.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

uri = os.environ["NEO4J_URI"]
auth = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
database = os.environ.get("NEO4J_DATABASE", "neo4j")

with GraphDatabase.driver(uri, auth=auth) as driver:
    driver.verify_connectivity()
    records, _, _ = driver.execute_query(
        "RETURN 1 AS ok, count { MATCH (n) } AS nodes", database_=database
    )
    row = records[0]
    print(f"Neo4j OK — uri={uri} database={database} nodes={row['nodes']}")

sys.exit(0)
