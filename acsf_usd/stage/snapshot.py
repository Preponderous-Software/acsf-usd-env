"""Author the non-time-varying part of an instance from its ``state.json`` snapshot.

Prims under ``/World/Instances/<name>`` (ARCHITECTURE.md § ACSF → USD mapping):
``Identity``, ``Mood`` (final value and baseline), ``Attention`` and
``Workspace/Item_<seq>``.
"""

from __future__ import annotations

from pxr import Sdf, Usd

from acsf_usd.model import Snapshot, WorkspaceItem
from acsf_usd.stage.common import set_attr, string_array, unique_names

T = Sdf.ValueTypeNames


def author_snapshot(
    stage: Usd.Stage,
    instance: Sdf.Path,
    snapshot: Snapshot,
    mood_names: dict[str, str] | None = None,
) -> None:
    identity = stage.DefinePrim(instance.AppendChild("Identity"))
    for attr_name, text in (
        ("acsf:identity:originStory", snapshot.origin_story),
        ("acsf:identity:purpose", snapshot.purpose),
        ("acsf:identity:selfConcept", snapshot.self_concept),
    ):
        if text is not None:
            set_attr(identity, attr_name, T.String, text)
    for attr_name, items in (
        ("acsf:identity:values", snapshot.values),
        ("acsf:identity:personalityTraits", snapshot.personality_traits),
        ("acsf:identity:amendments", snapshot.amendments),
    ):
        set_attr(identity, attr_name, T.StringArray, string_array(items))

    mood = stage.DefinePrim(instance.AppendChild("Mood"))
    # One mapping for both maps, so a dimension keeps the same name in both attributes and
    # dimensions that sanitise alike (``self-confidence`` / ``self_confidence``) stay distinct.
    dims = mood_names or unique_names([*snapshot.mood, *snapshot.initial_mood])
    for dim, value in sorted(snapshot.mood.items()):
        set_attr(mood, f"acsf:mood:{dims[dim]}", T.Double, value)
    for dim, value in sorted(snapshot.initial_mood.items()):
        set_attr(mood, f"acsf:moodBaseline:{dims[dim]}", T.Double, value)

    attention = stage.DefinePrim(instance.AppendChild("Attention"))
    if snapshot.attention_focus is not None:
        set_attr(attention, "acsf:focus", T.Token, snapshot.attention_focus)
    if snapshot.attention_theme is not None:
        set_attr(attention, "acsf:theme", T.String, snapshot.attention_theme)
    if snapshot.attention_salience is not None:
        set_attr(attention, "acsf:salience", T.Double, snapshot.attention_salience)

    workspace = stage.DefinePrim(instance.AppendChild("Workspace"))
    for item_name, item in workspace_item_names(snapshot.short_term):
        prim = stage.DefinePrim(workspace.GetPath().AppendChild(item_name))
        set_attr(prim, "acsf:kind", T.Token, item.kind)
        set_attr(prim, "acsf:content", T.String, item.content)
        set_attr(prim, "acsf:importance", T.Double, item.importance)
        set_attr(prim, "acsf:timestamp", T.String, item.timestamp)


def workspace_item_names(items: tuple[WorkspaceItem, ...]) -> list[tuple[str, WorkspaceItem]]:
    """Name items ``Item_<seq>``; when any ``seq`` is missing or repeated (``state.json``
    files written before ACSF #201 have none), every item is named by its list position."""
    seqs = [item.seq for item in items]
    if all(seq is not None and seq >= 0 for seq in seqs) and len(set(seqs)) == len(seqs):
        return [(f"Item_{item.seq}", item) for item in items]
    return [(f"Item_{index}", item) for index, item in enumerate(items)]
