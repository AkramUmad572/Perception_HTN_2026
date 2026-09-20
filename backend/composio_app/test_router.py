#!/usr/bin/env python3
from datetime import date

from composio_app.router import route_composio

_TODAY = date.today().strftime("%Y/%m/%d")


def test_named() -> None:
    cases = [
        ("pull up my google drive photos", "pull_app", ["googledrive"], "photos"),
        ("find me the picture of pikachu_keychain in my google drive photos", "pull_app", ["googledrive"], "photos"),
        ("find pikachu in my google drive", "pull_app", ["googledrive"], "photos"),
        ("pull up my google photos", "pull_app", ["googlephotos"], "photos"),
        ("pull up notion", "pull_app", ["notion"], "list"),
        ("hey, percy, can you pull up my Notion?", "pull_app", ["notion"], "list"),
        ("can you pull my last email", "pull_app", ["gmail"], "last_email"),
        ("hey, percy, can you pull up my last email from gmail?", "pull_app", ["gmail"], "last_email"),
        ("give me context on my emails from the past 2 days related to this object", "pull_app", ["gmail"], "email_search"),
        ("pull up emails about tesla from the last 2 days", "pull_app", ["gmail"], "email_search"),
        ("go through all of my photos across all apps and find pikachu", "image_find", None, "photos"),
        ("I don't have slack — wait, pull slack", "clarify", None, None),
        ("yeah build that", "build_from_brief", None, None),
        ("hey percy, what are your connections", "list_connections", None, None),
        ("what connections do you have", "list_connections", None, None),
        ("which apps are you connected to", "list_connections", None, None),
        ("list your connections", "list_connections", None, None),
        ("what integrations are you connected to", "list_connections", None, None),
        ("Export this to STL.", "publish_work", ["googledrive"], "publish"),
        ("Export this model for 3d printing.", "publish_work", ["googledrive"], "publish"),
        ("Send this model to Omer saying we finished.", "publish_work", ["gmail"], "publish"),
        ("Store it in the HTN folder and name it pikachu_keychain.", "publish_work", ["googledrive"], "publish"),
        ("Email Omer saying we finished the model, best regards from Umad.", "publish_work", ["gmail"], "publish"),
        (
            "Export this to STL and email Omer saying we finished the model, attach it, best regards from Umad.",
            "publish_work",
            ["gmail"],
            "publish",
        ),
        (
            "Export to STL, put it in HTN as pikachu_keychain, and email Omer saying we finished",
            "publish_work",
            ["googledrive", "gmail"],
            "publish",
        ),
        ("email them saying we finished", "clarify", None, None),
    ]
    fail = 0
    for text, action, apps, kind in cases:
        got = route_composio(text, has_brief=True)
        if not got or got.action != action:
            print("FAIL", text, "got", getattr(got, "action", None))
            fail += 1
            continue
        if apps and got.apps != apps:
            print("FAIL apps", text, got.apps)
            fail += 1
            continue
        if kind and got.pull_kind != kind:
            print("FAIL kind", text, got.pull_kind)
            fail += 1
            continue
        print("ok", text, "→", got.action, got.apps, got.pull_kind)
    hint = route_composio(
        "give me context on my emails from the past 2 days related to this object",
        object_hint="a yellow pikachu keychain",
    )
    if not hint or hint.photo_query != "pikachu keychain" or (hint.params or {}).get("days") != 2:
        print("FAIL object hint", getattr(hint, "photo_query", None), getattr(hint, "params", None))
        fail += 1
    else:
        print("ok object hint →", hint.photo_query, hint.params)
    tesla = route_composio("pull up emails about tesla from the last 2 days")
    if not tesla or tesla.photo_query != "tesla":
        print("FAIL tesla topic", getattr(tesla, "photo_query", None))
        fail += 1
    else:
        print("ok tesla topic →", tesla.photo_query)
    spoken = route_composio(
        "give me context on my email from today any emails from today pertaining to hack the north",
        object_hint="a bulbasaur keychain",
    )
    if not spoken or "hack" not in (spoken.photo_query or "") or "north" not in (spoken.photo_query or ""):
        print("FAIL spoken topic", getattr(spoken, "photo_query", None))
        fail += 1
    else:
        print("ok spoken topic →", spoken.photo_query, spoken.params)
    hyphen = route_composio("tell me about any emails pertaining to hyphen north today")
    if not hyphen or "hack" not in (hyphen.photo_query or ""):
        print("FAIL hyphen topic", getattr(hyphen, "photo_query", None))
        fail += 1
    else:
        print("ok hyphen topic →", hyphen.photo_query)
    hacks = route_composio("give me context on any emails pertaining to hacks on north from today")
    if not hacks or hacks.photo_query != "hack north":
        print("FAIL hacks topic", getattr(hacks, "photo_query", None))
        fail += 1
    else:
        print("ok hacks topic →", hacks.photo_query)
    hours = route_composio("give me context on any emails pertaining to hack the north from the past twenty-four hours")
    if not hours or hours.photo_query != "hack north" or (hours.params or {}).get("days") != 1:
        print("FAIL hours topic", getattr(hours, "photo_query", None), getattr(hours, "params", None))
        fail += 1
    else:
        print("ok hours topic →", hours.photo_query, hours.params)
    hockton = route_composio("give me context on any emails pertaining to Hockton North for September 19th")
    if (
        not hockton
        or hockton.photo_query != "hack north"
        or hockton.pull_kind != "email_search"
        or (hockton.params or {}).get("on_date") != "2026/09/19"
    ):
        print("FAIL hockton", getattr(hockton, "photo_query", None), getattr(hockton, "params", None))
        fail += 1
    else:
        print("ok hockton →", hockton.photo_query, hockton.params)
    notes = route_composio("can you give me any context about hacks and notes for September 19th?")
    if (
        not notes
        or notes.pull_kind != "email_search"
        or notes.photo_query != "hack north"
        or (notes.params or {}).get("on_date") != "2026/09/19"
    ):
        print("FAIL notes", getattr(notes, "photo_query", None), getattr(notes, "params", None))
        fail += 1
    else:
        print("ok notes →", notes.photo_query, notes.params)
    today = route_composio("emails about tesla from today")
    if not today or (today.params or {}).get("on_date") != _TODAY:
        print("FAIL today date", getattr(today, "params", None))
        fail += 1
    else:
        print("ok today date →", today.params)
    ranged = route_composio("emails about tesla from the last 2 days")
    if not ranged or ranged.params.get("on_date") or ranged.params.get("days") != 2:
        print("FAIL range", getattr(ranged, "params", None))
        fail += 1
    else:
        print("ok range →", ranged.params)
    unnamed = route_composio("email them saying we finished")
    if not unnamed or unnamed.action != "clarify" or "Who" not in (unnamed.reply or ""):
        print("FAIL unnamed email", getattr(unnamed, "action", None), getattr(unnamed, "reply", None))
        fail += 1
    else:
        print("ok unnamed email clarify")
    combo = route_composio(
        "Export this to STL and email Omer saying we finished the model please take a look, attach it, and send best regards from Umad."
    )
    if not combo or combo.params.get("email_body") != "we finished the model please take a look":
        print("FAIL combo body", getattr(combo, "params", None))
        fail += 1
    else:
        print("ok combo body →", combo.params.get("email_body"))
    slack_plus = route_composio("Export this to STL and slack @everyone")
    if not slack_plus or slack_plus.action != "publish_work":
        print("FAIL slack ignored", getattr(slack_plus, "action", None))
        fail += 1
    else:
        print("ok slack ignored on export")
    if fail:
        raise SystemExit(fail)


if __name__ == "__main__":
    test_named()
