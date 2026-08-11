"""
investigation.py — diagnosis as a normal action, and its evidence as a screen.

The workflow this page implements, in this order:

    analysis status
        ranked candidates, or insufficient evidence
            why this candidate was considered
            what attribution would require
            evidence
            temporal evidence
            cross-family evidence
            confounders
    considered and rejected
    what could not be assessed, and why
    conclusion, impact, containment, recommended checks
    export

**The abstention is the headline, not a footnote.** At its declared level this
engine abstains on every dataset this project can build, and the honest way to
show that is at the top of the page in the same typeface a conclusion would
have used. A product that buried it — or that quietly showed the top-ranked
candidate as though it had been offered — would be making the interface answer
the question before the engine did.

**Nothing here decides anything.** The ticks and crosses come from
`fabapp.explain`, which restates fields of `fabops.investigation/v1` and cites
the JSON path of each one. The impact, the containment ranking and the
recommended checks are `fabops.report`'s, and they are absent exactly when it
says they are: no subject, no impact, no recommendation.
"""
from __future__ import annotations

import streamlit as st

from fabapp import service
from fabapp.explain import (explain_candidate, explain_investigation,
                            families_of, leading_families)

#: The "no subject" entry, and the default. A cost question has to be asked
#: deliberately; a screen that opened with one already chosen would be showing
#: an impact estimate for an entity nobody named.
NO_SUBJECT = "—"


@st.cache_data(show_spinner="Estimating exposure and loss…")
def _decision_with_subject(db_path: str, stamp: float, subject: str):
    """The decision artifact for a subject the *user* named.

    Cached on the database's own modification time, like the main pass, so a
    regenerated dataset is a different entry rather than a stale answer.
    """
    return service.decision_for(db_path, subject=subject)


def _status(outcome, artifact, record) -> None:
    if outcome.insufficient_evidence:
        st.warning(f"### {outcome.headline}\n\n{outcome.reason}.")
        st.caption(
            "On a fault-free dataset this is the only correct answer, and it "
            "is a first-class one. It is also what this engine returns on "
            "every dataset in the scenario library at its declared level — "
            "the measured benchmark is in the README and is not flattering.")
    else:
        st.success(f"### {outcome.headline}\n\n{outcome.reason}.")

    columns = st.columns(4)
    columns[0].metric("family-wise p", outcome.p_familywise)
    columns[1].metric("declared level α", outcome.alpha)
    columns[2].metric("candidates assessed", outcome.assessed)
    columns[3].metric("not assessable", outcome.not_assessable)

    window = outcome.window
    st.caption(
        f"engine {artifact['generated_by']} · window "
        f"{window.get('start_day')}–{window.get('end_day')} d in "
        f"{window.get('bin_days')}-day bins · {outcome.permutations} "
        f"permutations · strata {', '.join(outcome.strata) or '—'} · "
        f"shared anchors at day "
        f"{', '.join(f'{day:.0f}' for day in outcome.anchors) or '—'}")

    if st.button("Re-run the diagnosis",
                 help="Clears the cached pass and runs the monitors and the "
                      "engine again. The engine draws no random numbers "
                      "outside its seeded permutation, so the result is the "
                      "same — which is the point of being able to press it."):
        st.cache_data.clear()
        st.rerun()


def _checklist(title: str, checks) -> None:
    st.markdown(f"**{title}**")
    for check in checks:
        mark = "✅" if check.met else "❌"
        st.markdown(f"{mark} **{check.claim}** — {check.evidence}")
        st.caption(f"`{check.source}`")


def _candidate_detail(artifact, candidate) -> None:
    explanation = explain_candidate(artifact, candidate)
    left, right = st.columns(2)
    with left:
        _checklist("Why this candidate was considered",
                   explanation.considered)
    with right:
        _checklist("What attribution would require",
                   explanation.attribution)

    st.caption(candidate["narrative"])

    evidence = list(candidate.get("evidence", ()))
    st.markdown("**Evidence**")
    if evidence:
        st.dataframe(
            [{"family": row["family"], "channel": row["channel"],
              "statistic": row["statistic"], "value": row["value"],
              "rank": f"{row['rank']} of {row['of']}",
              "support": row["support"], "comparison": row["comparison"]}
             for row in evidence], width="stretch", hide_index=True)
    else:
        st.info("this candidate carries no scorable evidence")

    left, right = st.columns(2)
    with left:
        st.markdown("**Temporal evidence**")
        onsets = list(candidate.get("onsets", ()))
        if onsets:
            st.dataframe(
                [{"onset (day)": row["day"],
                  "interval": f"{row['interval'][0]:.0f} – "
                              f"{row['interval'][1]:.0f}",
                  "proposed by": row["anchor_channel"]}
                 for row in onsets], width="stretch", hide_index=True)
            st.caption(
                "An onset is reported as an interval one bin either side of a "
                "shared anchor. The anchors are chosen once for the whole fab "
                "and every candidate is scored at all of them, so no candidate "
                "picked the change point it is measured at.")
        else:
            st.info("no anchor at which this candidate departed upwards")

    with right:
        st.markdown("**Cross-family evidence**")
        families = families_of(candidate)
        leading = set(leading_families(candidate))
        if families:
            st.dataframe(
                [{"family": family,
                  "leads its peers": "yes" if family in leading else "no",
                  "best rank": min(row["rank"] for row in evidence
                                   if row["family"] == family),
                  "channels": sum(1 for row in evidence
                                  if row["family"] == family)}
                 for family in families], width="stretch", hide_index=True)
            st.caption(
                "Families are combined by Fisher and channels inside a family "
                "are not, because channels in one family read the same physics "
                "and move together. Convergence across families is what the "
                "engine is built on; one family alone cannot clear the level.")
        else:
            st.info("no evidence family could be scored")

    confounders = list(candidate.get("confounders", ()))
    st.markdown("**Confounders controlled**")
    if confounders:
        st.dataframe(confounders, width="stretch", hide_index=True)
        st.caption(
            "The standing rival for an equipment candidate is that its "
            "exposure mix moved rather than its behaviour. The same statistic "
            "is recomputed on observations residualized against their own "
            "product and week, and both numbers are reported.")
    else:
        st.info("no exposure-mix control applies to this candidate kind")


def _subject_panel(artifact, record) -> str | None:
    """Let an engineer ask the counterfactual the engine will not answer.

    The engine abstains, so it names no subject, so impact and containment are
    empty. This is the supported way to fill them: an engineer says *assume it
    is this one*, and the artifact records that the subject was **supplied**
    rather than concluded. Those are different claims and the screen keeps them
    apart — the question is phrased as a cost of acting, never as a finding.
    """
    choices = service.subject_candidates(artifact)
    if not choices:
        return None

    with st.expander("Ask what acting on a candidate would cost", expanded=False):
        st.caption(
            "The engine named no subject. You can still ask what *would* "
            "follow from acting on one: exposed wafers, the within-product "
            "deficit against the same-role peers, the lots to contain first "
            "and the checks the knowledge table recommends. This is your "
            "hypothesis, not the engine's conclusion, and the exported "
            "artifact records it as supplied.")
        chosen = st.selectbox("Assume the subject is",
                              (NO_SUBJECT, *choices))
        return None if chosen == NO_SUBJECT else chosen


def _decision(decision, supplied: bool) -> None:
    st.subheader("Conclusion")
    subject = decision["subject"]
    if subject is None:
        st.info(
            "**No subject.** The engine named none, so there is no impact "
            "estimate, no containment list and no recommendation. That is the "
            "honest output of an engine that will not name what it cannot "
            "support — and the panel above asks the cost question anyway, on "
            "a subject you choose.")
    else:
        if supplied:
            st.warning(
                f"**{subject['id']} was supplied, not concluded.** Everything "
                f"below quantifies the consequence of acting on it. The "
                f"engine's own verdict is unchanged and is at the top of this "
                f"page.")
        st.write(f"subject **{subject['id']}** ({subject['kind']}), "
                 f"source: {subject['source']}")
        impact = decision["impact"]
        if impact is not None:
            columns = st.columns(3)
            columns[0].metric("exposed wafers", impact["exposed_wafers"])
            columns[1].metric("within-product deficit (pts)",
                              f"{impact['within_product_deficit_pts']:+.3f}",
                              delta=f"± {impact['standard_error_pts']:.3f}")
            columns[2].metric("standing vs peers (z)",
                              f"{impact['standing_z_among_peers']:+.2f}")
            st.caption(impact["note"])
            if not impact["distinguishable_from_benign_variation"]:
                st.warning(
                    "This deficit is inside the spread this fab's healthy "
                    "chambers show. Treat the die figure as an upper bound on "
                    "what containment could recover, not as a loss.")
            st.markdown("**Containment — lots by exposure**")
            st.dataframe(decision["containment"]["lots_ranked_by_exposure"],
                         width="stretch", hide_index=True)

    st.markdown("**Recommended checks**")
    if decision["actions"]:
        st.dataframe(decision["actions"], width="stretch", hide_index=True)
    else:
        st.info("no checks are recommended, because no subject was named")
    st.caption(f"knowledge table: {decision['provenance']['knowledge']}")


def render(data, record) -> None:
    decision = data["investigation"]
    artifact = decision["investigation"]
    outcome = explain_investigation(artifact)

    _status(outcome, artifact, record)
    st.divider()

    assessed = [c for c in artifact["candidates"] if c["status"] == "assessed"]
    st.subheader("Ranked candidates")
    if not assessed:
        st.info("No candidate could be scored on this dataset. The reasons "
                "are below, grouped.")
    else:
        st.dataframe(
            [{"kind": c["entity"]["kind"], "entity": c["entity"]["id"],
              "p": c["p_value"], "score": round(c["score"], 3),
              "families led": len(leading_families(c)),
              "onsets": ", ".join(str(o["day"]) for o in c["onsets"])}
             for c in assessed[:15]], width="stretch", hide_index=True)
        if outcome.insufficient_evidence:
            st.caption(
                "These are ranked, not offered. The engine did not clear its "
                "own family-wise level on this dataset, so none of them is a "
                "conclusion — the ranking is shown so the evidence can be "
                "read, not so a name can be taken from the top of it.")

        chosen = st.selectbox(
            "Examine a candidate",
            [c["entity"]["id"] for c in assessed[:15]])
        candidate = next(c for c in assessed if c["entity"]["id"] == chosen)
        _candidate_detail(artifact, candidate)

    st.divider()
    st.subheader("Considered and rejected")
    considered = list(artifact.get("considered", ()))
    if considered:
        st.dataframe(
            [{"kind": row["entity"]["kind"], "entity": row["entity"]["id"],
              "verdict": row["verdict"], "detail": row["detail"]}
             for row in considered], width="stretch", hide_index=True)
        st.caption(
            "A report that offers a candidate must also say which rivals it "
            "examined and why they lost — including rivals of a different "
            "kind, because a chamber and a product are competing explanations "
            "of the same rows.")
    else:
        st.info("no rivals were recorded, because no candidate was offered")

    if outcome.not_assessable_reasons:
        st.subheader("Could not be assessed")
        st.dataframe(
            [{"candidates": count, "reason": reason}
             for reason, count in outcome.not_assessable_reasons],
            width="stretch", hide_index=True)
        st.caption(
            "A hypothesis the data cannot score is carried with the reason it "
            "could not be scored. Dropping it would let a competing "
            "explanation be rejected without ever being asked.")

    st.divider()
    subject = _subject_panel(artifact, record)
    shown = decision
    if subject is not None:
        shown = _decision_with_subject(
            str(record.db_path), record.db_path.stat().st_mtime, subject)
    _decision(shown, supplied=subject is not None)

    st.divider()
    st.subheader("Export")
    name, text = service.artifact_text(shown)
    st.download_button(
        "Download the decision artifact (JSON)", data=text, file_name=name,
        mime="application/json")
    st.caption(
        "`fabops.report/v1`, embedding `fabops.investigation/v1` verbatim, and "
        "byte-identical to what `fabops-report` writes for the same dataset. "
        "This is exactly the document shown above — including whether the "
        "subject was concluded or supplied. It is the file-based export the "
        "FabKG boundary defines: one versioned document, written and "
        "forgotten, with no consumer this repository knows about.")
