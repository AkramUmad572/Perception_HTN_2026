#!/usr/bin/env python3
from composio_app.publish_parse import choose_export_format, compose_email_body, parse_publish, resolve_recipient


def test_export_only() -> None:
    spec = parse_publish("Export this to STL.")
    assert spec, "export should route"
    assert spec["format"] == "stl"
    assert spec["drive"] is True
    assert spec["gmail"] is False
    assert spec["wants_email"] is False
    print("ok export_only")


def test_drive_named() -> None:
    spec = parse_publish("Store it in the HTN folder and name it pikachu_keychain.")
    assert spec
    assert spec["drive"] is True
    assert spec["folder"] == "htn"
    assert spec["filename"] == "pikachu_keychain"
    assert spec["gmail"] is False
    put = parse_publish("Export to STL, put it in HTN as pikachu_keychain, and email Omer saying we finished")
    assert put and put["folder"] == "htn" and put["filename"] == "pikachu_keychain" and put["drive"]
    print("ok drive_named")


def test_email_named() -> None:
    spec = parse_publish("Email Omer saying we finished the model, best regards from Umad.")
    assert spec
    assert spec["recipient"] == "Omer"
    assert spec["format"] == "step"
    assert spec["format_reason"] == "send"
    assert spec["gmail"] is True
    assert spec["drive"] is False
    assert spec["email_body"] == "we finished the model"
    assert spec["signoff"] and "Umad" in spec["signoff"]
    # A plain "email X saying Y" must never require a model to attach.
    assert spec["attach"] is False
    assert spec["export"] is False
    body = compose_email_body(spec)
    assert "we finished the model" in body
    assert "export" not in body.lower()
    assert "Umad" in body
    print("ok email_named")


def test_combined_export_email() -> None:
    text = (
        "Export this to STL and email Omer saying we finished the model please take a look, "
        "attach it, and send best regards from Umad."
    )
    spec = parse_publish(text)
    assert spec
    assert spec["format"] == "stl"
    assert spec["drive"] is False
    assert spec["gmail"] is True
    assert spec["recipient"] == "Omer"
    assert spec["email_body"] == "we finished the model please take a look"
    assert "export" not in spec["email_body"].lower()
    assert "stl" not in spec["email_body"].lower()
    assert "attach" not in spec["email_body"].lower()
    assert spec["attach"] is True
    assert spec["export"] is True
    print("ok combined_export_email")


def test_address_plus_drive() -> None:
    spec = parse_publish(
        "Email omer.sjd05@gmail.com we finished the keychain and store it in the HTN folder as pikachu_keychain."
    )
    assert spec
    assert spec["recipient"] == "omer.sjd05@gmail.com"
    assert spec["folder"] == "htn"
    assert spec["filename"] == "pikachu_keychain"
    assert spec["drive"] is True
    assert spec["gmail"] is True
    assert spec["format"] == "step"
    assert spec["email_body"] == "we finished the keychain"
    assert "store" not in spec["email_body"].lower()
    assert "folder" not in spec["email_body"].lower()
    print("ok address_plus_drive")


def test_send_email_to_name() -> None:
    spec = parse_publish(
        "can you send an email to omer from gmail saying that i finished making the cube?"
    )
    assert spec
    assert spec["recipient"] == "omer"
    assert spec["format"] == "step"
    assert spec["gmail"] is True
    assert "finished making the cube" in (spec["email_body"] or "")
    assert spec["recipient"] not in ("to", "saying", "an")
    print("ok send_email_to_name")


def test_unnamed_email() -> None:
    spec = parse_publish("email them saying we finished")
    assert spec
    assert spec["wants_email"] is True
    assert spec["recipient"] is None
    assert spec["gmail"] is False
    print("ok unnamed_email")


def test_explicit_attach_without_export_words() -> None:
    spec = parse_publish("send this model to Omer saying take a look")
    assert spec
    assert spec["gmail"] is True
    assert spec["recipient"] == "Omer"
    assert spec["attach"] is True
    assert spec["export"] is True
    print("ok explicit_attach_without_export_words")


def test_not_publish() -> None:
    assert parse_publish("pull up my last email") is None
    assert parse_publish("pull up emails about tesla from the last 2 days") is None
    assert parse_publish("find pikachu in my google drive") is None
    print("ok not_publish")


def test_step() -> None:
    spec = parse_publish("Export this to STEP.")
    assert spec and spec["format"] == "step"
    print("ok step")


def test_print_vs_send_format() -> None:
    print_spec = parse_publish("Export this model for 3d printing.")
    assert print_spec and print_spec["format"] == "stl" and print_spec["format_reason"] == "print"
    assert print_spec["drive"] is True and print_spec["gmail"] is False
    send_spec = parse_publish("Send this model to Omer saying we finished the model")
    assert send_spec and send_spec["format"] == "step" and send_spec["recipient"] == "Omer"
    both = parse_publish("Export this for 3d printing and email Omer saying we finished")
    assert both and both["formats"] == ["stl", "step"] and both["format_reason"] == "both"
    assert both["gmail"] is True and both["drive"] is True
    explicit = parse_publish("Email Omer this as an STL saying we finished")
    assert explicit and explicit["format"] == "stl" and explicit["format_explicit"] is True
    assert choose_export_format("share this on notion", wants_email=False) == ("step", "send")
    print("ok print_vs_send_format")


def test_resolve_recipient() -> None:
    items = [{
        "sender_name": "Omer",
        "subtitle": "Omer <omer.sjd05@gmail.com>",
        "title": "keychain",
        "body": "hi",
    }]
    assert resolve_recipient("Omer", items) == "omer.sjd05@gmail.com"
    assert resolve_recipient("omer.sjd05@gmail.com", []) == "omer.sjd05@gmail.com"
    assert resolve_recipient("them", items) is None
    assert resolve_recipient("Omer", []) is None
    print("ok resolve_recipient")


if __name__ == "__main__":
    test_export_only()
    test_drive_named()
    test_email_named()
    test_combined_export_email()
    test_address_plus_drive()
    test_send_email_to_name()
    test_unnamed_email()
    test_explicit_attach_without_export_words()
    test_not_publish()
    test_step()
    test_print_vs_send_format()
    test_resolve_recipient()
    print("all publish parse tests passed")
