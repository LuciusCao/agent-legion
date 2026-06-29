from types import MappingProxyType

VIDEO_KNOWLEDGE_ARTIFACTS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "download": ("source.mp4",),
        "transcribe": ("subtitles.srt", "transcription.json"),
        "subtitle_review": ("subtitles_reviewed.srt", "subtitle_review_report.json"),
        "chapter_generate": ("chapters_raw.json", "chapters.json"),
        "interaction_generate": ("interactions.json",),
        "content_review": ("checklist.json", "review_result.json"),
        "assemble": ("metadata.json", "report.md", "upload_params.json"),
        "package": ("package_manifest.json",),
    }
)


def artifact_owners() -> dict[str, str]:
    owners: dict[str, str] = {}
    for node_key, names in VIDEO_KNOWLEDGE_ARTIFACTS.items():
        for name in names:
            if name in owners:
                raise ValueError(f"artifact {name!r} has multiple owners")
            owners[name] = node_key
    return owners
