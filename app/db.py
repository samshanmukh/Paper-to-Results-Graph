"""Shared Neo4j driver setup (Aura + macOS certifi fix)."""

import os

import certifi

# macOS python.org builds ship without system CA certs; must be set before
# the driver opens any TLS connection.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# Generic domain labels are not an ownership boundary in a shared database.
# Every Verigraph node also carries this label/property pair; cleanup and reads
# must require both so another application can safely use labels such as Paper.
GRAPH_OWNER_LABEL = "Verigraph"
GRAPH_NAMESPACE = os.environ.get("VERIGRAPH_GRAPH_NAMESPACE", "verigraph").strip()
if not GRAPH_NAMESPACE or len(GRAPH_NAMESPACE) > 128:
    raise RuntimeError("VERIGRAPH_GRAPH_NAMESPACE must be 1-128 characters")

OUR_LABELS = ["Paper", "Author", "Claim", "Method", "Dataset", "Task", "Run", "Artifact"]


def get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
