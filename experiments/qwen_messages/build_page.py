"""Build the analysis page: the template with the analysis data, the quoted messages and the narrative embedded.

    python experiments/qwen_messages/build_page.py ANALYSIS.json QUOTES.json NARRATIVE.html TEMPLATE.html OUT.html

QUOTES.json is a list of {"rep", "seed", "day", "round", "sender", "starts" (optional), "note"}; each names exactly
one message of the arm with channels on (a sender can send several in a round: "starts", the opening of its text,
tells them apart), whose delivered text is taken from ANALYSIS.json, so no quote is retyped by hand.
NARRATIVE.html holds the page's prose as sections, each opened by a line `<!--NAME-->` that names the template
marker it fills (INTRO, FINDINGS, MESSAGES, METHOD); every marker must be filled.
"""

import json
import re
import sys


def main(analysis_path, quotes_path, narrative_path, template_path, out_path):
    data = json.load(open(analysis_path))
    quotes = []
    for q in json.load(open(quotes_path)):
        found = [m for m in data["messages"]
                 if (m["rep"], m["seed"], m["day"], m["round"], m["sender"]) ==
                 (q["rep"], q["seed"], q["day"], q["round"], q["sender"])
                 and m["text"].startswith(q.get("starts", ""))]
        if len(found) != 1:
            raise SystemExit(f"{len(found)} messages match quote {q}: give a longer 'starts'")
        m = found[0]
        quotes.append({**{k: m[k] for k in ("rep", "seed", "day", "round", "sender", "sender_role", "to", "text")},
                       "note": q["note"]})
    page = {"episodes": data["episodes"], "comparison": data["comparison"],
            "message_figures": data["message_figures"], "quotes": quotes}
    # Model-written text goes in last, with "</" and "<!--" escaped, so it can neither close the script element
    # nor be taken for a narrative marker.
    blob = json.dumps(page, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\u0021--")
    html = open(template_path).read()
    assert html.count("__DATA__") == 1
    parts = re.split(r"^<!--([A-Z]+)-->\n", open(narrative_path).read(), flags=re.M)
    for name, text in zip(parts[1::2], parts[2::2]):
        marker = f"<!--{name}-->"
        if html.count(marker) != 1:
            raise SystemExit(f"the template has no single marker {marker}")
        html = html.replace(marker, text.strip())
    left = re.findall(r"<!--[A-Z]+-->", html)
    if left:
        raise SystemExit(f"markers left unfilled: {left}")
    html = html.replace("__DATA__", blob)
    open(out_path, "w").write(html)
    print(f"wrote {out_path}: {len(html)} bytes, {len(quotes)} quotes")


if __name__ == "__main__":
    main(*sys.argv[1:])
