import hashlib

from sales_mcp.privacy import initials, k_anon, redact, sha256_hex


def test_address_never_leaves():
    out = redact(
        {
            "city": "Quito",
            "address_text": "Av. Amazonas N24-123",
            "delivery_address": "Calle Sucre 1",
            "lat": -0.18,
            "lng": -78.48,
        }
    )
    assert out == {"city": "Quito"}


def test_national_id_and_secrets_never_leave():
    assert redact({"national_id": "1712345678", "token": "abc", "password": "x"}) == {}


def test_email_leaves_hashed_never_in_clear():
    out = redact({"email": "Ana@Example.com "})
    assert "email" not in out
    assert out["hash_email"] == hashlib.sha256(b"ana@example.com").hexdigest()


def test_phone_is_hashed_as_digits_with_country_code():
    out = redact({"phone": "+593 00 123 4567"})
    assert out == {"hash_phone": hashlib.sha256(b"593001234567").hexdigest()}


def test_hash_is_the_one_meta_expects_whatever_the_formatting():
    # Same person, typed two ways, must match the same Meta audience member.
    assert sha256_hex("  ANA@example.COM", "email") == sha256_hex("ana@example.com", "email")
    assert sha256_hex("+593 (00) 123-4567", "phone") == sha256_hex("593001234567", "phone")


def test_a_missing_email_still_has_the_hashed_shape():
    # Without this, a customer with no email would come back with a plain
    # `email: null` key, and a reader could not tell "has none" from "leaked".
    assert redact({"email": None}) == {"hash_email": None}


def test_names_shrink_to_initials():
    assert redact({"customer_name": "Ana María Pérez"}) == {"customer_name": "A. M. P."}
    assert initials("") == ""


def test_small_counts_of_people_do_not_identify_anyone():
    out = redact({"by_city": {"Quito": 12, "Puyo": 1, "Loja": 4}})
    assert out["by_city"] == {"Quito": 12, "Puyo": "< 5", "Loja": "< 5"}


def test_k_anonymity_edge():
    assert k_anon(4) == "< 5"
    assert k_anon(5) == 5
    assert k_anon(None) is None


def test_breakdowns_of_things_as_lists_keep_their_counts():
    # The trap: "7 units" must not become "< 5". Things travel as lists.
    out = redact({"by_channel": [{"channel": "WEB", "sales": 3}]})
    assert out == {"by_channel": [{"channel": "WEB", "sales": 3}]}


def test_redacts_at_any_depth():
    out = redact({"items": [{"order": {"customer_email": "a@b.co", "address": "x"}}]})
    assert out == {"items": [{"order": {"hash_email": sha256_hex("a@b.co", "email")}}]}


def test_everything_else_passes_untouched():
    data = {"product": "Serum", "revenue": "12.30", "flags": [True, False], "n": 3}
    assert redact(data) == data


def test_booleans_under_by_are_not_counts():
    assert redact({"by_flag": {"x": True}}) == {"by_flag": {"x": True}}
