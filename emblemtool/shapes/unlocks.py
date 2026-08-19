"""Flag emblems that use shapes the game may refuse if they are still locked."""
from .shape_id_map import known_ids


def layer_risks(layers):
    """Return ['ranks'] and/or ['weapons'] when those categories appear."""
    cats = set()
    for raw in layers or []:
        try:
            sid = int(raw.get("shape", -1))
        except (TypeError, ValueError):
            continue
        label = known_ids.get(sid)
        if not label:
            continue
        cats.add(label.split("/", 1)[0])
    out = []
    if "ranks" in cats:
        out.append("ranks")
    if "gear" in cats:
        out.append("weapons")
    return out
