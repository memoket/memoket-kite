import io
import json
import tarfile
import zipfile

import pytest

from benchmarks.longmemeval.evaluate import _pack_units
from benchmarks.reproduce import package as release_package
from benchmarks.reproduce import prepare, verify
from benchmarks.reproduce.common import load_manifest, sha256


def test_release_manifest_pins_its_datasets_and_declares_the_scores_it_claims():
    """The manifest is a claim, not a scratchpad.

    A declared score is what `verify.py` holds a downloaded release to and what
    `package.py` refuses to overwrite, so a swapped or re-judged result
    directory cannot ship under this version number. `status` stays unearned in
    the checked-in file: packaging sets it to "verified" only after every seal
    checks out.
    """
    manifest = load_manifest()
    assert manifest["repository"] == "https://github.com/memoket/memoket-kite"
    assert set(manifest["datasets"]) == {"locomo", "longmemeval"}
    for dataset in manifest["datasets"].values():
        assert len(dataset["revision"]) == 40
        assert len(dataset["sha256"]) == 64
        assert dataset["url"].startswith("https://")
        assert dataset["license"] and dataset["derived_artifacts"]
    assert manifest["release"]["status"] != "verified"
    assert manifest["release"]["git_commit"] is None
    assert manifest["models"]["resolved_snapshot"] == "gpt-4.1-mini-2025-04-14"
    for item in manifest["benchmarks"].values():
        assert isinstance(item["score"], float) and 0.0 < item["score"] <= 1.0
        assert item["asset"].startswith("memoket-kite-")
        # What a downloader hashes the asset against before unpacking it. The
        # archive does not exist until packaging builds it, so the digest is
        # absent until then; a digest carried over from an earlier run would
        # describe bytes nobody is going to ship. The rule is therefore
        # "correct or absent", never stale — and once `status` is "verified"
        # the archive exists, so the digest must be present.
        digest = item.get("asset_sha256")
        if manifest["release"]["status"] == "verified":
            assert digest is not None, "a packaged release must pin every asset digest"
        if digest is not None:
            assert len(digest) == 64


def test_download_verifies_hash_before_publishing(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified bytes")
    destination = tmp_path / "output" / "download.bin"

    prepare._download(source.as_uri(), destination, sha256(source))
    assert destination.read_bytes() == source.read_bytes()

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        prepare._download(source.as_uri(), destination, "0" * 64)
    assert destination.read_bytes() == source.read_bytes()


def test_archive_extraction_rejects_parent_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../outside.txt")
        payload = b"unsafe"
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))

    with pytest.raises(RuntimeError, match="unsafe archive member"):
        prepare._safe_extract(archive, tmp_path / "target")
    assert not (tmp_path / "outside.txt").exists()


def test_release_artifacts_restore_into_the_configured_root(tmp_path, monkeypatch):
    root = tmp_path / "isolated"
    archive = root / "downloads" / "v-test" / "locomo.tar.gz"
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, "w:gz") as bundle:
        for name, value in {
            "artifacts/codebooks/locomo/example.xml": b"<codebook />",
            "artifacts/results/locomo-sample/judged.jsonl": b"{}\n",
            "artifacts/cache/plans/locomo-sample/plan.json": b"{}\n",
            "licenses/third-party/synthetic.txt": b"license fixture\n",
            "LICENSE-DATA.md": b"license index\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            bundle.addfile(info, io.BytesIO(value))
    monkeypatch.setattr(prepare, "ARTIFACTS_ROOT", root)
    prepare.prepare_artifacts(
        {
            "release": {"version": "v-test"},
            "benchmarks": {"locomo": {"asset": archive.name, "asset_sha256": sha256(archive)}},
        }
    )
    assert (root / "codebooks/locomo/example.xml").read_bytes() == b"<codebook />"
    assert (root / "results/locomo-sample/judged.jsonl").is_file()
    assert (root / "cache/plans/locomo-sample/plan.json").is_file()
    assert (root / "licenses/third-party/synthetic.txt").is_file()
    assert (root / "LICENSE-DATA.md").is_file()
    assert not (root / "artifacts").exists()


def test_offline_verification_recomputes_manifest_score(corpus, tmp_path, monkeypatch):
    """Verification goes through the judge seal, not around it.

    Reading judged.jsonl directly and recomputing the verdicts already in it
    accepts any edit that keeps the row count. Verification therefore takes the
    same route a release does, which is why the fixture here has to be a fully
    sealed artifact rather than a hand-written file.
    """
    from benchmarks.common import judging, publish
    from benchmarks.reproduce import sealed

    codebooks = tmp_path / "codebooks"
    codebooks.mkdir()
    (codebooks / "one.xml").write_text("<codebook />", encoding="utf-8")
    result_dir = tmp_path / "locomo-v0"
    result_dir.mkdir()
    (result_dir / "results_a.jsonl").write_text('{"qa_idx": 0}\n', encoding="utf-8")
    # a real corpus binding: the release path checks the digest, not the count
    from benchmarks.common import manifest as run_manifest
    from benchmarks.locomo.evaluate import DATASET as LOCOMO_DATASET

    corpus_sha = run_manifest.corpus_digest([LOCOMO_DATASET, codebooks])
    (result_dir / "manifest.json").write_text(
        json.dumps({"samples": [0], "corpus_sha": corpus_sha}) + "\n", encoding="utf-8"
    )
    judged = (
        json.dumps(
            {
                "category": 1,
                "question": "q",
                "gold": "a",
                "answer": "a",
                "J": 1.0,
                "gold_evidence": [],
            }
        )
        + "\n"
    )
    publish.publish(
        result_dir,
        judged=judged,
        score={"overall": {"accuracy": 1.0}},
        manifest=lambda stamped: judging.write_manifest(
            result_dir,
            model="gpt-4.1-mini",
            source=judging.read_snapshot([result_dir / "results_a.jsonl"]),
            judged=result_dir / "judged.jsonl",
            protocol=sealed.PROTOCOLS["locomo"],
            run=result_dir / "manifest.json",
            score=result_dir / "score.json",
            sealed=stamped,
        ),
    )

    paths = {"codebooks": codebooks, "results": result_dir / "judged.jsonl"}
    monkeypatch.setattr(verify, "artifact_path", lambda value: paths[value])
    monkeypatch.setattr(verify, "dataset_path", lambda value: LOCOMO_DATASET)
    manifest = {
        "datasets": {"locomo": {"path": "dataset", "sha256": sha256(LOCOMO_DATASET)}},
        "benchmarks": {
            "locomo": {
                "codebook_path": "codebooks",
                "result_path": "results",
                "codebooks": 1,
                "result_rows": 1,
                "metric": "accuracy",
                "score": 1.0,
            }
        },
    }

    metrics = verify.verify_benchmark("locomo", manifest)
    assert metrics["overall"]["accuracy"] == 1.0
    manifest["datasets"]["locomo"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="pinned dataset is missing or has the wrong checksum"):
        verify.verify_benchmark("locomo", manifest)


def test_longmemeval_pack_units_includes_aligned_instance_mentions():
    rows = [
        {"id": "fact-sessionF2", "type": "fact", "src": "fact-session:0"},
        {"id": "line-session:3", "type": "line", "src": "line-session:3"},
        {
            "id": "I1",
            "type": "instance",
            "src": "instance-aF1 instance-bF12 instance-aF3",
        },
        {"id": "", "type": "count", "src": "ignoredF1"},
        {"id": "I2", "type": "instance", "src": ""},
        {"id": "answer_sharegpt_5m7gg5F_0", "type": "unit", "src": ""},
        {
            "id": "answer_sharegpt_5m7gg5F_0F1",
            "type": "fact",
            "src": "answer_sharegpt_5m7gg5F_0:1",
        },
    ]

    assert _pack_units(rows) == [
        "answer_sharegpt_5m7gg5F_0",
        "fact-session",
        "instance-a",
        "instance-b",
        "line-session",
    ]


def test_release_archive_contains_only_explicit_paths(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    included = repository / "artifacts" / "codebooks" / "locomo"
    unrelated = repository / "artifacts" / "results" / "old-run"
    included.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    (included / "one.xml").write_text("<codebook />", encoding="utf-8")
    (unrelated / "private.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(release_package, "REPO_ROOT", repository)
    archive = tmp_path / "release.tar.gz"

    # A plan cache lives wherever the run pointed `--plan-cache`, which need
    # not be inside the repository; the archive still unpacks it at the layout
    # the release documents.
    outside = tmp_path / "elsewhere" / "plans"
    outside.mkdir(parents=True)
    (outside / "plan.json").write_text("{}", encoding="utf-8")

    release_package._archive(
        [
            (included, "artifacts/codebooks/locomo"),
            (outside, "artifacts/cache/plans/locomo-v0.1.0"),
        ],
        archive,
    )

    with tarfile.open(archive, "r:gz") as bundle:
        names = bundle.getnames()
    assert "artifacts/codebooks/locomo/one.xml" in names
    assert "artifacts/cache/plans/locomo-v0.1.0/plan.json" in names
    assert not any("old-run" in name or "elsewhere" in name for name in names)


def test_packaging_refuses_credential_shaped_content(tmp_path):
    """A release that ships a token-shaped string fails secret scanners on
    every mirror it lands on. Benchmark corpora are transcripts of real
    conversations and can legitimately contain such a string, so the gate is a
    mechanical shape check rather than a judgement about whether the credential
    is live: anything token-shaped stops the release.
    """
    import pytest

    from benchmarks.reproduce import package

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.xml").write_text("<codebook>harmless</codebook>")
    package._refuse_secrets([(clean, "codebooks")])  # must not raise

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "b.xml").write_text('access_token = "gho_' + "A" * 36 + '"')
    with pytest.raises(RuntimeError, match="credential-shaped"):
        package._refuse_secrets([(dirty, "codebooks")])

    pem = tmp_path / "pem"
    pem.mkdir()
    # Keep the repository itself free of a contiguous key marker while still
    # constructing one in the temporary negative fixture.
    (pem / "k.txt").write_text("-----BEGIN RSA " + "PRIVATE KEY-----\nxxxx")
    with pytest.raises(RuntimeError, match="credential-shaped"):
        package._refuse_secrets([(pem, "keys")])


def test_archive_itself_refuses_credentials(tmp_path):
    """The gate lives inside `_archive`, not beside it.

    A scan called separately at the packaging site is one line away from being
    disarmed, and nothing downstream would notice. Placing it on the only path
    that writes an archive means every caller is covered by construction, and
    the archive is not created when it fires.
    """
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "b.xml").write_text('access_token = "gho_' + "A" * 36 + '"')
    archive = tmp_path / "release.tar.gz"
    with pytest.raises(RuntimeError, match="credential-shaped"):
        release_package._archive([(dirty, "artifacts/codebooks/locomo")], archive)
    assert not archive.exists()


def test_release_directory_allowlist_rejects_benign_private_files(tmp_path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (result_dir / "private-notes.txt").write_text("not a secret token", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unexpected.*private-notes"):
        release_package._require_directory_files(result_dir, {"manifest.json"})


def test_release_refuses_codebooks_the_published_loader_cannot_open(tmp_path):
    codebooks = tmp_path / "codebooks"
    codebooks.mkdir()
    valid = codebooks / "valid.xml"
    valid.write_text(
        """<codebook><vocab/><timeline><session id="s1" date="2026-01-01">
        <line id="s1L1">hello</line></session></timeline></codebook>""",
        encoding="utf-8",
    )
    assert release_package._require_loadable_codebooks("demo", codebooks, 1) == [valid]

    valid.write_text(
        """<codebook><vocab><topic/></vocab><timeline>
        <session id="s1" date="2026-01-01"><line id="s1L1">hello</line></session>
        </timeline></codebook>""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="fails the current loader"):
        release_package._require_loadable_codebooks("demo", codebooks, 1)


def test_secret_scan_reads_inside_containers_and_echoes_no_token(tmp_path):
    """A token inside a wheel or tarball never appears in the container's
    compressed bytes, so a scan that reads containers as opaque files passes
    exactly the artifacts most likely to carry one. The scan therefore reads
    inside them — and the refusal names the kind of credential without echoing
    it, since the message travels into logs and CI output.
    """
    token = "gho_" + "A" * 36
    inner = tmp_path / "leaky.txt"
    inner.write_text(f'access_token = "{token}"')
    bundle = tmp_path / "results.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        tar.add(inner, arcname="run/leaky.txt")
    assert token.encode() not in bundle.read_bytes()  # flat scan would pass
    with pytest.raises(RuntimeError, match="credential-shaped") as excinfo:
        release_package._refuse_secrets([(bundle, "artifacts")])
    message = str(excinfo.value)
    assert "GitHub token" in message
    assert token not in message
    assert "gho_" not in message

    openai_token = "sk-" + "Z" * 32
    with pytest.raises(RuntimeError, match="OpenAI-style API key") as excinfo:
        release_package._refuse_secret_payload(openai_token.encode())
    assert openai_token not in str(excinfo.value)
    assert "sk-" not in str(excinfo.value)


def test_secret_scan_reads_tar_pax_metadata(tmp_path):
    token = "ghp_" + "A" * 24
    bundle = tmp_path / "metadata.tar.gz"
    with tarfile.open(bundle, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("safe.txt")
        member.size = 4
        member.pax_headers = {"comment": token}
        archive.addfile(member, io.BytesIO(b"safe"))

    with pytest.raises(RuntimeError, match="GitHub token") as excinfo:
        release_package._refuse_secrets([(bundle, "artifact")])

    assert token not in str(excinfo.value)


def _minimal_distribution_pair(root, *, wheel_payload=b"harmless"):
    dist = root / "dist"
    dist.mkdir(parents=True)
    sdist = dist / "memoket_kite-0.1.0.tar.gz"
    with tarfile.open(sdist, "w:gz"):
        pass
    wheel = dist / "memoket_kite-0.1.0-py3-none-any.whl"
    import zipfile

    with zipfile.ZipFile(wheel, "w") as bundle:
        bundle.writestr("payload.txt", wheel_payload)
    return sdist, wheel


def _minimal_release_manifest():
    return {
        "release": {"version": "v0.1.0"},
        "datasets": {},
        "benchmarks": {},
    }


def test_failed_release_leaves_no_verified_manifest_or_old_checksums(tmp_path, monkeypatch):
    """A secret discovered at the last gate cannot leave a publishable-looking tree."""
    tree = tmp_path / "repo"
    token = b"gho_" + b"A" * 36
    pair = _minimal_distribution_pair(tree, wheel_payload=token)
    output = tmp_path / "release"

    monkeypatch.setattr(release_package, "REPO_ROOT", tree)
    monkeypatch.setattr(release_package, "load_manifest", lambda _path: _minimal_release_manifest())
    monkeypatch.setattr(release_package, "_released_commit", lambda _manifest, **_: "a" * 40)
    monkeypatch.setattr(release_package, "_source_commit", lambda: "b" * 40)
    monkeypatch.setattr(release_package, "_require_fresh_distributions", lambda _built: pair)

    with pytest.raises(RuntimeError, match="GitHub token") as excinfo:
        release_package.package_release("v0.1.0", output, tmp_path / "manifest.json")
    assert token.decode() not in str(excinfo.value)
    assert not output.exists()


def test_release_refuses_a_nonempty_output_without_deleting_it(tmp_path):
    output = tmp_path / "important-project"
    output.mkdir()
    sentinel = output / "only-copy.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    with pytest.raises(RuntimeError, match="absent or empty"):
        release_package.package_release("v0.1.0", output, tmp_path / "manifest.json")

    assert sentinel.read_text(encoding="utf-8") == "keep me"


def test_successful_release_atomically_promotes_from_an_empty_directory(tmp_path, monkeypatch):
    tree = tmp_path / "repo"
    pair = _minimal_distribution_pair(tree)
    output = tmp_path / "release"
    output.mkdir()

    monkeypatch.setattr(release_package, "REPO_ROOT", tree)
    monkeypatch.setattr(release_package, "load_manifest", lambda _path: _minimal_release_manifest())
    monkeypatch.setattr(release_package, "_released_commit", lambda _manifest, **_: "a" * 40)
    monkeypatch.setattr(release_package, "_source_commit", lambda: "b" * 40)
    monkeypatch.setattr(release_package, "_require_fresh_distributions", lambda _built: pair)

    manifest = release_package.package_release("v0.1.0", output, tmp_path / "manifest.json")
    assert manifest["release"]["status"] == "verified"
    assert (output / "artifact-manifest.json").is_file()
    assert (output / "SHA256SUMS").read_text().count("\n") == 3


def test_shipped_distributions_carry_no_builder_identity(tmp_path):
    """`python -m build` stamps tar members with the maintainer's username,
    uid/gid and wall-clock mtimes — identity the release would otherwise
    publish. The scrub removes all of it, leaves payloads byte-identical, and
    is idempotent, so packaging the same input twice pins the same hash and the
    manifest's asset digest stays reproducible.
    """
    import io as io_module
    import zipfile

    sdist = tmp_path / "pkg-0.1.0.tar.gz"
    payload = b"print('hello')\n"
    with tarfile.open(sdist, "w:gz") as bundle:
        member = tarfile.TarInfo("pkg-0.1.0/src/pkg/mod.py")
        member.size = len(payload)
        member.uid, member.gid = 501, 20
        member.uname, member.gname = "leakyuser", "staff"
        member.mtime = 1_786_433_497
        bundle.addfile(member, io_module.BytesIO(payload))

    wheel = tmp_path / "pkg-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as bundle:
        info = zipfile.ZipInfo("pkg/mod.py", date_time=(2026, 8, 11, 4, 51, 37))
        bundle.writestr(info, payload)

    release_package._strip_builder_identity(sdist, wheel)
    first = (sdist.read_bytes(), wheel.read_bytes())

    with tarfile.open(sdist) as bundle:
        member = bundle.getmembers()[0]
        assert (member.uname, member.gname, member.uid, member.gid) == ("", "", 0, 0)
        assert member.mtime == 0
        assert bundle.extractfile(member).read() == payload
    with zipfile.ZipFile(wheel) as bundle:
        info = bundle.infolist()[0]
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert bundle.read(info) == payload

    release_package._strip_builder_identity(sdist, wheel)
    assert (sdist.read_bytes(), wheel.read_bytes()) == first  # deterministic


def test_packaging_can_waive_an_unverifiable_commit(tmp_path, monkeypatch):
    """A bundle whose repository history is not published records provenance,
    not a claim anyone can check: the SHA cannot be checked out, so demanding
    that it be reachable protects an invariant no reader can use. The waiver is
    explicit, so a bundle never passes itself off as commit-verified by default.

    The fixture is built here rather than read from `artifacts/`, which is not
    part of a clone: a test that needs a local run to exist is a test that only
    passes on the machine that produced one.
    """
    results = {}
    for name, commit in (("locomo", "a" * 40), ("longmemeval", "b" * 40)):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "manifest.json").write_text(
            json.dumps({"commit": commit, "dirty": False}), encoding="utf-8"
        )
        results[name] = directory / "judged.jsonl"
    monkeypatch.setattr(release_package, "artifact_path", lambda value: results[value])
    # The packager's own tree is checked first; that is a separate rule, and
    # this test is about which commits the results agree on.
    monkeypatch.setattr(release_package, "_pending_measured_changes", lambda: "")
    manifest = {"benchmarks": {name: {"result_path": name} for name in results}}

    with pytest.raises(RuntimeError, match="different commits"):
        release_package._released_commit(manifest)
    assert release_package._released_commit(manifest, allow_unreachable=True) == "a" * 40


def test_normalising_a_distribution_twice_yields_the_same_bytes(tmp_path):
    """A release digest has to describe the source, not the build machine.

    A tar member carries its time in the header and again in its PAX record,
    and a gzip container carries the name it was written under. Normalising one
    and not the others leaves two builds of the same tree differing by exactly
    those bytes, which no pinned digest can survive.
    """
    payload = b"x" * 32
    digests = []
    for run in range(2):
        sdist = tmp_path / f"dist-{run}.tar.gz"
        with tarfile.open(sdist, "w:gz") as bundle:
            info = tarfile.TarInfo("pkg-0/src/pkg/__init__.py")
            info.size = len(payload)
            info.mtime = 1_700_000_000 + run  # a wall clock, one second apart
            info.uid = info.gid = 501 + run
            info.uname, info.gname = f"builder{run}", f"staff{run}"
            info.pax_headers = {"mtime": f"{info.mtime}.123456{run}"}
            bundle.addfile(info, io.BytesIO(payload))
        wheel = tmp_path / f"dist-{run}.whl"
        with zipfile.ZipFile(wheel, "w") as bundle:
            entry = zipfile.ZipInfo("pkg/__init__.py", date_time=(2020 + run, 1, 1, 0, 0, 0))
            bundle.writestr(entry, payload)
        release_package._strip_builder_identity(sdist, wheel)
        digests.append((sha256(sdist), sha256(wheel)))
    assert digests[0] == digests[1]
