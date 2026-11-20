"""`scripts/api_role.py`: the service role's password, as Postgres keeps it, and
the URL Render takes. Offline: the script's own check is what logs in."""

import base64
import hashlib
import hmac
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "api_role.py"
spec = importlib.util.spec_from_file_location("api_role", SCRIPT)
api_role = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_role)


def test_the_verifier_is_scram_sha_256_as_rfc_7677_works_it():
    """RFC 7677's own exchange: a server holding this verifier would accept the
    client's proof and answer with the RFC's signature."""
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = api_role.scram_verifier("pencil", salt)
    method, rest = verifier.split("$", 1)
    count_salt, keys = rest.split("$")
    iterations, said_salt = count_salt.split(":")
    stored_key, server_key = (base64.b64decode(part) for part in keys.split(":"))
    assert (method, iterations, said_salt) == ("SCRAM-SHA-256", "4096",
                                               "W22ZaJ0SNY7soEsUEjb6gQ==")

    auth = (b"n=user,r=rOprNGfwEbeRWgbNEkqO,"
            b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
            b"s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
            b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0")
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_signature = hmac.new(stored_key, auth, "sha256").digest()
    client_key = bytes(a ^ b for a, b in zip(proof, client_signature))
    assert hashlib.sha256(client_key).digest() == stored_key
    assert base64.b64encode(hmac.new(server_key, auth, "sha256").digest()) == \
        b"6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_a_verifier_never_carries_a_quote():
    for _ in range(50):
        assert "'" not in api_role.scram_verifier("x", api_role.secrets.token_bytes(16))


def test_the_url_is_the_pooler_url_as_the_role():
    admin = ("postgresql://postgres.abcdefgh:hunter2@aws-0-eu-central-1.pooler.supabase.com"
             ":5432/postgres?sslmode=require")
    assert api_role.service_url(admin, "a/b+c:d@e") == (
        "postgresql://helixpeek_api.abcdefgh:a%2Fb%2Bc%3Ad%40e"
        "@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require")


def test_a_url_with_no_project_ref_names_the_role_alone():
    assert api_role.service_url("postgresql://postgres@127.0.0.1:54329/postgres", "pw") == \
        "postgresql://helixpeek_api:pw@127.0.0.1:54329/postgres"
