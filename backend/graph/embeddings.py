"""Offline feature-hash vectors for Graphiti's non-null vector storage contract.

Controlled evidence queries do not rely on semantic similarity. Feature hashing
also keeps optional extraction usable without a remote embedding service.
"""
import hashlib
import math
import re
from graphiti_core.embedder.client import EmbedderClient


def hash_embedding(text: str) -> list[float]:
    vector = [0.] * 1024
    for token in re.findall(r"\w+", text.lower()) or ["empty"]:
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % 1024] += 1 if digest[4] % 2 else -1
    norm = math.sqrt(sum(v*v for v in vector)) or 1
    return [v/norm for v in vector]


class LocalHashEmbedder(EmbedderClient):
    async def create(self, input_data):
        return hash_embedding(input_data if isinstance(input_data, str) else str(input_data))

    async def create_batch(self, input_data_list):
        return [hash_embedding(text) for text in input_data_list]
