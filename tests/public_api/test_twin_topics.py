from memoket_kite.core.vocab import Vocab
from memoket_kite.tools.twin_topics import organize_topics


def test_topic_groups_merge_equivalents_and_keep_their_cited_spans():
    source = {
        "facts": [],
        "taxonomy": [],
        "index_spans": [
            {"topics": ["Remote internship pay"], "segment_ids": ["s1"], "entities": ["Cedar"]},
            {
                "topics": ["Intern compensation"],
                "segment_ids": ["s2"],
                "facets": {"object": ["pay"]},
            },
            {"topics": ["Work schedule"], "segment_ids": ["s3"]},
        ],
    }

    def group(prompt, shape):
        assert "Remote internship pay" in prompt and "Work schedule" in prompt
        return {
            "groups": [
                {"canonical": "T1", "members": ["T1", "T2"], "parent": "Remote internship"},
                {"canonical": "T3", "members": ["T3"], "parent": "Remote internship"},
            ]
        }

    organized = organize_topics(source, Vocab(), group)
    spans = organized["index_spans"]
    assert [row["topics"] for row in spans] == [
        ["remote_internship_pay"],
        ["remote_internship_pay"],
        ["work_schedule"],
    ]
    assert [{k: v for k, v in row.items() if k != "topics"} for row in spans] == [
        {k: v for k, v in row.items() if k != "topics"} for row in source["index_spans"]
    ]
    assert organized["taxonomy"] == [
        {
            "name": "remote_internship_pay",
            "parents": ["remote_internship"],
            "aliases": ["intern_compensation"],
        },
        {"name": "work_schedule", "parents": ["remote_internship"], "aliases": []},
    ]


def test_topic_groups_do_not_replace_existing_topics_or_create_cycles():
    vocab = Vocab()
    for code in ("rollout", "deployment", "project delivery"):
        vocab.define_root(code)
    vocab.move_topic("project_delivery", ["rollout"])
    source = {
        "facts": [],
        "taxonomy": [],
        "index_spans": [
            {"topics": [topic], "segment_ids": [str(i)]}
            for i, topic in enumerate(("Rollout", "Deployment"))
        ],
    }

    def group(prompt, shape):
        assert "known_topic" in prompt
        return {
            "groups": [{"canonical": "T1", "members": ["T1", "T2"], "parent": "Project delivery"}]
        }

    organized = organize_topics(source, vocab, group)
    assert [row["topics"] for row in organized["index_spans"]] == [["rollout"], ["deployment"]]
    assert organized["taxonomy"] == [
        {"name": "deployment", "parents": ["project_delivery"], "aliases": []},
        {"name": "rollout", "parents": [], "aliases": []},
    ]


def test_provider_row_reference_resolves_to_a_topic_label():
    annotations = {
        "facts": [],
        "taxonomy": [],
        "index_spans": [
            {"topics": [name], "segment_ids": [str(i)]}
            for i, name in enumerate(("Seasonal promotions", "Launch schedule", "Discounts"))
        ],
    }

    def group(prompt, shape):
        return {
            "groups": [
                {"canonical": "T1", "members": ["T1"], "parent": None},
                {"canonical": "T2", "members": ["T2"], "parent": "T1"},
                {"canonical": "T3", "members": ["T3"], "parent": "T99"},
            ]
        }

    result = organize_topics(annotations, Vocab(), group)
    assert {row["name"]: row["parents"] for row in result["taxonomy"]} == {
        "seasonal_promotions": [],
        "launch_schedule": ["seasonal_promotions"],
        "discounts": [],
    }


def test_a_single_topic_can_reuse_a_supported_workspace_topic_without_global_alias():
    vocab = Vocab()
    vocab.define_root("intern compensation")
    vocab.define_root("sensor testing")
    annotations = {
        "facts": [],
        "taxonomy": [],
        "index_spans": [
            {"topics": ["remote intern pay"], "segment_ids": ["s1"]},
        ],
    }

    def group(prompt, shape):
        assert '"id": "K1", "name": "intern_compensation"' in prompt
        assert "sensor_testing" not in prompt
        return {"groups": [{"canonical": "K1", "members": ["T1"], "parent": None}]}

    organized = organize_topics(annotations, vocab, group)
    assert organized["index_spans"] == [{"topics": ["intern_compensation"], "segment_ids": ["s1"]}]
    assert organized["taxonomy"] == [{"name": "intern_compensation", "parents": [], "aliases": []}]
