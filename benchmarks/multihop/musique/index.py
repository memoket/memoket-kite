"""Build fresh retrieval metadata for the MuSiQue shared passage corpus."""

from collections import Counter
from copy import deepcopy
from string import ascii_lowercase, digits

from jsonschema import Draft202012Validator

from benchmarks.multihop import passage_full, passage_index, profile
from memoket_kite.core.vocab import norm_code


def child_code_pattern(roots):
    """Match valid codes except exact root names, using portable regex syntax."""
    def suffix(excluded, first=False):
        initials = sorted({word[0] for word in excluded if word})
        alphabet = ascii_lowercase if first else ascii_lowercase + digits + "_"
        other = "".join(char for char in alphabet if char not in initials)
        branches = [""] if not first and "" not in excluded else []
        if other:
            branches.append(f"[{other}][a-z0-9_]*")
        for char in initials:
            branches.append(char + suffix([word[1:] for word in excluded if word.startswith(char)]))
        return "(?:" + "|".join(branches) + ")"

    return "^" + suffix(roots, first=True) + "$"


FORMAT = deepcopy(passage_index.FORMAT)
SCHEMA = FORMAT["json_schema"]["schema"]
ROOTS = tuple(profile.SEED_ROOTS)
PROMPT = passage_index.PROMPT.replace(
    "Resolve aliases only after listing entities. Both endpoints must be listed.",
    "Group explicitly equivalent names under one canonical entity and one type. "
    "Assign each name to only one entity group, including aliases. "
    "For up to four explicit locations that characterize the passage, "
    "set place_facet to true on their entity group.",
).replace(
    "punctuation and title qualifiers. Do not supply outside knowledge.",
    "punctuation and title qualifiers. Copy a single contiguous name from the source. "
    "Never complete a coordinated phrase, expand a nationality adjective, "
    "or attach a qualifier that is not part of that exact name. Omit uncertain names.",
)

properties = SCHEMA["properties"]
old_entity = properties["entities"]["items"]["properties"]
name_group = {
    "name": old_entity["name"],
    "aliases": {
        **passage_index.arr(passage_index.STRING),
        "description": "Other verbatim names for this same referent. Use [] if absent.",
    },
}
non_place = deepcopy(old_entity["type"])
non_place["enum"].remove("place")
properties["entities"]["items"] = {
    "anyOf": [
        passage_index.obj({**name_group, "type": non_place}),
        passage_index.obj({
            **name_group,
            "type": {"type": "string", "enum": ["place"]},
            "place_facet": {"type": "boolean"},
        }),
    ],
}
properties.pop("aliases")
SCHEMA["required"].remove("aliases")
properties["facets"]["properties"].pop("place")
properties["facets"]["required"].remove("place")
SCHEMA["properties"]["topics"]["items"] = {
    "anyOf": [
        passage_index.obj({
            "code": {"type": "string", "enum": list(ROOTS)},
            "parent": {"type": "null"},
        }),
        passage_index.obj({
            "code": {"type": "string", "pattern": child_code_pattern(ROOTS)},
            "parent": {"type": "string", "enum": list(ROOTS)},
        }),
    ],
    "description": "A root has null parent. A new child has one of the roots as its parent.",
}
WIRE_VALIDATOR = Draft202012Validator(SCHEMA)


def project_metadata(data, source):
    """Admit grounded, unambiguous claims without changing any model values."""
    WIRE_VALIDATOR.validate(data)
    entities = []
    aliases = []
    places = []
    rejected = []
    groups = []
    for group in data["entities"]:
        canonical = group["name"]
        if not norm_code(canonical) or not passage_index.surface_in_source(canonical, source):
            rejected.append({"field": "entity", "name": canonical, "reason": "absent_from_source"})
            continue
        groups.append(group)

    canonical_counts = Counter(norm_code(group["name"]) for group in groups)
    alias_counts = Counter(
        code for group in groups for code in {norm_code(name) for name in group["aliases"]}
        if code != norm_code(group["name"])
    )
    for group in groups:
        canonical = group["name"]
        code = norm_code(canonical)
        if canonical_counts[code] > 1:
            rejected.append({"field": "entity", "name": canonical, "reason": "ambiguous_canonical"})
            continue
        entities.append({"name": canonical, "type": group["type"]})
        for name in dict.fromkeys(group["aliases"]):
            alias_code = norm_code(name)
            if alias_code == code:
                continue  # KITE already treats equivalent spellings as one key.
            if not alias_code or not passage_index.surface_in_source(name, source):
                rejected.append({"field": "alias", "name": name, "reason": "absent_from_source"})
            elif alias_code in canonical_counts or alias_counts[alias_code] > 1:
                rejected.append({"field": "alias", "name": name, "reason": "ambiguous_alias"})
            else:
                entities.append({"name": name, "type": group["type"]})
                aliases.append({"canonical": canonical, "alias": name})
        if group.get("place_facet"):
            places.append(canonical)
    if len(places) > passage_index.SCHEMA["properties"]["facets"]["properties"]["place"]["maxItems"]:
        rejected.append({"field": "place_facet", "names": places, "reason": "over_limit"})
        places = []
    native = {
        "entities": entities,
        "aliases": aliases,
        "topics": data["topics"],
        "facets": {**data["facets"], "place": places},
    }
    return passage_index.validate(native, source), rejected


def admit_metadata(vocab, metadata):
    conflicts = list(passage_index.alias_conflicts(vocab, metadata))
    if not conflicts:
        return metadata, []

    # The conflicting spelling already denotes a different registry entry.
    # Avoid attributing this passage or its place facet to that entry.
    blocked = {norm_code(edge["alias"]) for edge in conflicts}
    admitted = {
        **metadata,
        "aliases": [edge for edge in metadata["aliases"] if norm_code(edge["alias"]) not in blocked],
        "entities": [entry for entry in metadata["entities"] if norm_code(entry["name"]) not in blocked],
        "facets": {
            **metadata["facets"],
            "place": [name for name in metadata["facets"]["place"] if norm_code(name) not in blocked],
        },
    }
    passage_index.check_alias_conflicts(vocab, admitted)
    return admitted, conflicts


if __name__ == "__main__":
    # Keep the projection importable by spawned index workers.
    from benchmarks.multihop.musique.index import project_metadata as project_index

    passage_full.main(prompt=PROMPT, response_format=FORMAT, project_metadata=project_index,
                      admit_metadata=admit_metadata)
