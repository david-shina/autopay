"""Pydantic request/response schemas (a.k.a. DTOs).

These are the *wire formats* — the public API surface. They are
separate from the SQLModel tables (`app/models`) because:
  * the API contract shouldn't leak every column (e.g. `hashed_password`,
    `bvn_ciphertext` are *never* in a response);
  * request shapes carry validation the model doesn't need;
  * response shapes can be aggregated across multiple models.
"""
