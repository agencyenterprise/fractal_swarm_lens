"""Optional text encoder; source/target pairing remains application-owned."""
import math
import os


class OpenAITextEncoder:
    model = "text-embedding-3-small"

    def __init__(self, client, *, dimensions=16, batch_size=128):
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 1536:
            raise ValueError("Embedding dimensions must be an integer in [1, 1536]")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 2048:
            raise ValueError("Batch size must be an integer in [1, 2048]")
        self.client, self.dimensions, self.batch_size = client, dimensions, batch_size

    @classmethod
    def from_env(cls, env_path=".env"):
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv(env_path, override=False)
        model = os.environ.get("CASPIAN_EMBEDDING_MODEL", cls.model)
        if model != cls.model:
            raise ValueError("This encoder requires text-embedding-3-small")
        dimensions = int(os.environ.get("CASPIAN_EMBEDDING_DIMENSIONS", "16"))
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required")
        return cls(OpenAI(), dimensions=dimensions)

    @property
    def feature_schema(self):
        return f"openai/{self.model}/dimensions={self.dimensions}/unaltered-text/v1"

    def describe(self):
        return {"provider": "openai", "model": self.model, "dimensions": self.dimensions,
                "feature_schema": self.feature_schema, "preprocessing": "none"}

    def encode(self, texts):
        texts = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be nonempty text strings")
        result = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch,
                                                     dimensions=self.dimensions, encoding_format="float")
            rows = sorted(response.data, key=lambda row: row.index)
            if [row.index for row in rows] != list(range(len(batch))):
                raise ValueError("Embedding response indices do not match the input batch")
            vectors = [tuple(float(value) for value in row.embedding) for row in rows]
            if any(len(vector) != self.dimensions or not all(math.isfinite(x) for x in vector)
                   for vector in vectors):
                raise ValueError("Embedding response has invalid dimensions or nonfinite values")
            result.extend(vectors)
        return result
