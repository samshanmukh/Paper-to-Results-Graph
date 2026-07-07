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

# This Aura instance is SHARED with the sceneshop project. Only ever create or
# delete nodes carrying these labels — never run label-less deletes.
OUR_LABELS = ["Paper", "Author", "Claim", "Method", "Dataset", "Task", "Run", "Artifact"]


def get_driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
