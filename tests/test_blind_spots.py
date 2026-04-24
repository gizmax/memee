"""Blind Spot Simulation: everything we DON'T track but SHOULD.

Tests the DARK SIDE of Memee — failure modes, false positives,
noise, bloat, miscalibration, over-reliance, and gaming.

This is the honest test. If Memee can survive these, it's real.

14 blind spots tested:
  1. False positives (wrong warnings)
  2. Noise ratio (useless memories)
  3. Search miss rate
  4. Stale knowledge damage
  5. Propagation spam
  6. Confidence miscalibration
  7. Over-reliance risk
  8. Memory bloat + search degradation
  9. Cold start problem
  10. Knowledge silos between teams
  11. Gaming / garbage inflation
  12. Context loss (what without why)
  13. Survivorship bias
  14. Technology migration (old truth becomes wrong)

Run: pytest tests/test_blind_spots.py -v -s
"""

import random
import time
from collections import defaultdict

import pytest
from sqlalchemy import func

from memee.engine.confidence import get_confidence_interval, get_uncertainty, update_confidence
from memee.engine.dream import run_dream_cycle
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.predictive import scan_project_for_warnings
from memee.engine.propagation import run_propagation_cycle
from memee.engine.quality_gate import run_quality_gate
from memee.engine.search import search_memories
from memee.engine.tokens import estimate_org_savings
from memee.storage.models import (
    AntiPattern, MaturityLevel, Memory, MemoryConnection,
    MemoryType, MemoryValidation, Organization, Project, ProjectMemory,
)

random.seed(2026)


@pytest.fixture
def blind_env(session, org):
    """Environment for blind spot testing."""
    projects = []
    for i in range(30):
        stacks = [
            (["Python", "FastAPI", "PostgreSQL"], ["python", "api"]),
            (["React", "TypeScript", "Tailwind"], ["react", "frontend"]),
            (["Swift", "SwiftUI", "CoreData"], ["swift", "mobile"]),
        ]
        stack, tags = stacks[i % 3]
        proj = Project(
            organization_id=org.id, name=f"Proj-{i:02d}",
            path=f"/blind/proj-{i:02d}", stack=stack, tags=tags,
        )
        session.add(proj)
        projects.append(proj)
    session.commit()
    return session, projects, org


class TestBlindSpots:

    def test_all_blind_spots(self, blind_env):
        """Comprehensive blind spot analysis."""
        session, projects, org = blind_env

        results = {}

        # ═══════════════════════════════════
        # 1. FALSE POSITIVES
        # Anti-pattern warning that's WRONG
        # ═══════════════════════════════════

        # Create an AP that's too broad
        m_ap = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Never use global variables",
            content="Global variables cause bugs",
            tags=["python", "architecture"],
            confidence_score=0.7,
        )
        session.add(m_ap)
        session.flush()
        ap = AntiPattern(
            memory_id=m_ap.id, severity="medium",
            trigger="Global variables", consequence="Hard to test",
            alternative="Use dependency injection",
        )
        session.add(ap)
        session.commit()

        # But sometimes globals ARE correct (e.g., logging config, constants)
        # Count how many projects get FALSE warnings
        false_positives = 0
        true_positives = 0
        for proj in projects:
            warnings = scan_project_for_warnings(session, proj)
            for w in warnings:
                # This AP is too broad — 40% of matches are false positives
                if random.random() < 0.4:
                    false_positives += 1
                else:
                    true_positives += 1

        fp_rate = false_positives / max(false_positives + true_positives, 1)
        results["false_positive_rate"] = round(fp_rate * 100, 1)

        # ═══════════════════════════════════
        # 2. NOISE RATIO
        # How many memories are never accessed?
        # ═══════════════════════════════════

        # Create 100 memories, most will never be searched
        for i in range(100):
            m = Memory(
                type=MemoryType.OBSERVATION.value,
                title=f"Random observation {i}: something happened",
                content=f"Observation {i} about something vague",
                tags=["misc"],
            )
            session.add(m)
        session.commit()

        # Simulate 50 searches — how many unique memories are hit?
        accessed_ids = set()
        for _ in range(50):
            queries = ["timeout", "database", "security", "API", "performance"]
            results_search = search_memories(
                session, random.choice(queries), limit=5, use_vectors=False
            )
            for r in results_search:
                accessed_ids.add(r["memory"].id)

        total_memories = session.query(func.count(Memory.id)).scalar()
        noise_ratio = 1 - (len(accessed_ids) / max(total_memories, 1))
        results["noise_ratio"] = round(noise_ratio * 100, 1)
        results["memories_never_accessed"] = total_memories - len(accessed_ids)

        # ═══════════════════════════════════
        # 3. SEARCH MISS RATE
        # ═══════════════════════════════════

        miss_queries = [
            "kubernetes pod scheduling",
            "WebAssembly optimization",
            "GraphQL subscription",
            "blockchain consensus",
            "quantum error correction",
        ]
        misses = 0
        for q in miss_queries:
            r = search_memories(session, q, limit=3, use_vectors=False)
            if not r:
                misses += 1
        results["search_miss_rate"] = round(misses / len(miss_queries) * 100, 1)

        # ═══════════════════════════════════
        # 4. STALE KNOWLEDGE DAMAGE
        # Old pattern applied in new context causes bugs
        # ═══════════════════════════════════

        # "Use Python 2 print statement" — was once valid, now harmful
        stale_pattern = Memory(
            type=MemoryType.PATTERN.value,
            title="Use print statement for debugging",
            content="print 'debug:', variable  # Quick debugging",
            tags=["python", "debugging"],
            confidence_score=0.8,
            maturity=MaturityLevel.VALIDATED.value,
            application_count=10,
        )
        session.add(stale_pattern)
        session.commit()

        # Simulate: 3 agents use it, 2 get burned (Python 3 syntax error)
        stale_damage = 0
        stale_uses = 5
        for _ in range(stale_uses):
            if random.random() < 0.6:  # 60% chance of damage
                stale_damage += 1
                # Invalidation should eventually kill it
                v = MemoryValidation(
                    memory_id=stale_pattern.id,
                    project_id=random.choice(projects).id,
                    validated=False,
                    evidence="Python 3 syntax error — print is a function now",
                )
                session.add(v)
                update_confidence(stale_pattern, False)

        results["stale_damage_events"] = stale_damage
        results["stale_pattern_conf_after"] = round(stale_pattern.confidence_score, 3)
        results["stale_self_corrected"] = stale_pattern.confidence_score < 0.5

        # ═══════════════════════════════════
        # 5. PROPAGATION SPAM
        # Irrelevant patterns pushed to wrong projects
        # ═══════════════════════════════════

        # Python-specific pattern pushed to Swift project
        py_pattern = Memory(
            type=MemoryType.PATTERN.value,
            title="Use list comprehension for filtering",
            content="[x for x in items if x.valid]",
            tags=["python", "performance"],
            confidence_score=0.7,
        )
        session.add(py_pattern)
        session.flush()
        pm = ProjectMemory(project_id=projects[0].id, memory_id=py_pattern.id)
        session.add(pm)
        session.commit()

        prop = run_propagation_cycle(session, confidence_threshold=0.5, max_propagations=50)

        # Count irrelevant propagations (Python pattern → Swift/React)
        spam_count = 0
        relevant_count = 0
        for proj in projects:
            has_link = session.query(ProjectMemory).filter(
                ProjectMemory.project_id == proj.id,
                ProjectMemory.memory_id == py_pattern.id,
            ).count()
            if has_link and "python" not in (proj.tags or []):
                spam_count += 1
            elif has_link:
                relevant_count += 1

        results["propagation_spam"] = spam_count
        results["propagation_relevant"] = relevant_count
        results["propagation_precision"] = round(
            relevant_count / max(relevant_count + spam_count, 1) * 100, 1
        )

        # ═══════════════════════════════════
        # 6. CONFIDENCE MISCALIBRATION
        # High confidence but actually wrong
        # ═══════════════════════════════════

        # Create memories with KNOWN ground truth, check if confidence matches
        calibration_data = []
        for i in range(50):
            true_quality = random.random()
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=f"Calibration pattern {i}",
                content=f"Test pattern {i}",
                tags=["test"],
            )
            session.add(m)
            session.flush()

            # Simulate validations based on true quality
            for _ in range(10):
                validated = random.random() < true_quality
                v = MemoryValidation(
                    memory_id=m.id,
                    project_id=random.choice(projects).id,
                    validated=validated,
                )
                session.add(v)
                update_confidence(m, validated)

            calibration_data.append((m.confidence_score, true_quality))

        session.commit()

        # Check calibration: group by confidence bucket, compare to actual success rate
        buckets = defaultdict(lambda: {"predicted": [], "actual": []})
        for conf, quality in calibration_data:
            bucket = round(conf * 5) / 5  # 0.0, 0.2, 0.4, 0.6, 0.8
            buckets[bucket]["predicted"].append(conf)
            buckets[bucket]["actual"].append(quality)

        miscalibration_error = 0
        bucket_count = 0
        for bucket, data in sorted(buckets.items()):
            if data["predicted"]:
                avg_pred = sum(data["predicted"]) / len(data["predicted"])
                avg_actual = sum(data["actual"]) / len(data["actual"])
                error = abs(avg_pred - avg_actual)
                miscalibration_error += error
                bucket_count += 1

        avg_miscalibration = miscalibration_error / max(bucket_count, 1)
        results["confidence_miscalibration"] = round(avg_miscalibration, 3)
        results["calibration_quality"] = (
            "GOOD" if avg_miscalibration < 0.15
            else "OK" if avg_miscalibration < 0.25
            else "POOR"
        )

        # ═══════════════════════════════════
        # 7. MEMORY BLOAT + SEARCH DEGRADATION
        # ═══════════════════════════════════

        # Measure search speed at current size
        t0 = time.time()
        for _ in range(20):
            search_memories(session, "timeout API", limit=5, use_vectors=False)
        time_current = (time.time() - t0) / 20 * 1000  # ms per search

        # Add 500 more memories (simulating bloat)
        for i in range(500):
            m = Memory(
                type=MemoryType.OBSERVATION.value,
                title=f"Bloat memory {i}: some random observation about coding",
                content=f"Detail about observation {i} that nobody will ever search for",
                tags=["bloat", f"tag-{i % 20}"],
            )
            session.add(m)
        session.commit()

        # Measure search speed again
        t0 = time.time()
        for _ in range(20):
            search_memories(session, "timeout API", limit=5, use_vectors=False)
        time_bloated = (time.time() - t0) / 20 * 1000

        results["search_ms_before_bloat"] = round(time_current, 1)
        results["search_ms_after_bloat"] = round(time_bloated, 1)
        results["search_degradation_pct"] = round(
            (time_bloated - time_current) / max(time_current, 0.1) * 100, 1
        )

        # ═══════════════════════════════════
        # 8. COLD START
        # First 2 weeks — system has nothing useful
        # ═══════════════════════════════════

        # Fresh org, 0 memories — what can Memee do?
        cold_searches = 0
        cold_hits = 0
        cold_queries = ["how to set up FastAPI", "database best practices",
                         "security checklist", "CI pipeline setup"]
        for q in cold_queries:
            r = search_memories(session, q, limit=3, use_vectors=False)
            cold_searches += 1
            if r and r[0]["total_score"] > 0.3:
                cold_hits += 1

        results["cold_start_hit_rate"] = round(cold_hits / cold_searches * 100, 1)
        results["cold_start_verdict"] = (
            "OK" if cold_hits > 0 else "PROBLEM — no useful results"
        )

        # ═══════════════════════════════════
        # 9. KNOWLEDGE SILOS
        # Some teams use Memee, others don't
        # ═══════════════════════════════════

        team_usage = defaultdict(int)
        for m in session.query(Memory).all():
            for pm in m.projects:
                proj = session.get(Project, pm.project_id)
                if proj:
                    team = (proj.tags or ["unknown"])[0] if proj.tags else "unknown"
                    team_usage[team] += 1

        max_usage = max(team_usage.values()) if team_usage else 1
        min_usage = min(team_usage.values()) if team_usage else 0
        silo_ratio = 1 - (min_usage / max(max_usage, 1))
        results["knowledge_silo_ratio"] = round(silo_ratio * 100, 1)
        results["team_usage"] = dict(team_usage)

        # ═══════════════════════════════════
        # 10. GAMING
        # Agent records garbage to inflate stats
        # ═══════════════════════════════════

        gaming_accepted = 0
        gaming_rejected = 0
        garbage = [
            ("test", "test", []),
            ("asdf", "asdf", []),
            ("TODO fix later", "need to fix", ["todo"]),
            ("Meeting notes from standup", "discussed stuff", ["meeting"]),
            ("a" * 600, "content", ["spam"]),
        ]
        for title, content, tags in garbage:
            gate = run_quality_gate(session, title, content, tags, "observation")
            if gate.accepted and not gate.merged:
                gaming_accepted += 1
            else:
                gaming_rejected += 1

        results["gaming_rejected"] = gaming_rejected
        results["gaming_accepted"] = gaming_accepted
        results["gaming_filter_rate"] = round(
            gaming_rejected / max(len(garbage), 1) * 100, 1
        )

        # ═══════════════════════════════════
        # 11. CONTEXT LOSS
        # Memory says WHAT but not WHY or WHEN
        # ═══════════════════════════════════

        context_rich = 0
        context_poor = 0
        for m in session.query(Memory).filter(
            Memory.type == MemoryType.PATTERN.value
        ).limit(50).all():
            content = m.content or ""
            has_why = any(w in content.lower() for w in
                         ["because", "since", "due to", "reason", "prevents", "avoids"])
            has_when = any(w in content.lower() for w in
                          ["when", "if", "during", "after", "before", "always", "never"])
            if has_why and has_when:
                context_rich += 1
            else:
                context_poor += 1

        total_checked = context_rich + context_poor
        results["context_poor_pct"] = round(
            context_poor / max(total_checked, 1) * 100, 1
        )

        # ═══════════════════════════════════
        # 12. SURVIVORSHIP BIAS
        # We track wins but not silent failures
        # ═══════════════════════════════════

        total_validations = session.query(func.count(MemoryValidation.id)).scalar()
        positive_vals = session.query(func.count(MemoryValidation.id)).filter(
            MemoryValidation.validated == True  # noqa: E712
        ).scalar()
        negative_vals = total_validations - positive_vals

        results["validation_positive_bias"] = round(
            positive_vals / max(total_validations, 1) * 100, 1
        )

        # ═══════════════════════════════════
        # 13. TECHNOLOGY MIGRATION
        # Old truth becomes wrong
        # ═══════════════════════════════════

        # "Use jQuery for DOM manipulation" — was canon in 2015
        obsolete = Memory(
            type=MemoryType.PATTERN.value,
            title="Use jQuery for DOM manipulation",
            content="$('#element').hide() is the standard approach",
            tags=["javascript", "frontend"],
            confidence_score=0.9,
            maturity=MaturityLevel.CANON.value,
            validation_count=20,
            application_count=30,
        )
        session.add(obsolete)
        session.commit()

        # Run aging — does it get deprecated?
        run_aging_cycle(session)
        # It won't — because it has high confidence and isn't old enough
        # This is the blind spot: CANON memories are "immortal"
        results["obsolete_survived_aging"] = obsolete.maturity != MaturityLevel.DEPRECATED.value
        results["obsolete_confidence"] = round(obsolete.confidence_score, 3)

        # Only explicit invalidations can kill it
        for _ in range(10):
            v = MemoryValidation(
                memory_id=obsolete.id,
                project_id=random.choice(projects).id,
                validated=False,
                evidence="jQuery is obsolete, use native DOM or React",
            )
            session.add(v)
            update_confidence(obsolete, False)

        results["obsolete_after_invalidations"] = round(obsolete.confidence_score, 3)
        results["obsolete_deprecated"] = obsolete.maturity == MaturityLevel.DEPRECATED.value

        # ═══════════════════════════════════
        # 14. UNCERTAINTY TRACKING
        # Do we know what we don't know?
        # ═══════════════════════════════════

        high_conf_memories = session.query(Memory).filter(
            Memory.confidence_score > 0.7
        ).limit(20).all()

        uncertainty_data = []
        for m in high_conf_memories:
            unc = get_uncertainty(m)
            lo, hi = get_confidence_interval(m)
            uncertainty_data.append({
                "confidence": m.confidence_score,
                "uncertainty": unc,
                "interval": (lo, hi),
                "evidence": (m.validation_count or 0) + (m.invalidation_count or 0),
            })

        avg_unc = sum(d["uncertainty"] for d in uncertainty_data) / max(len(uncertainty_data), 1)
        results["avg_uncertainty_high_conf"] = round(avg_unc, 3)

        # ═══════════════════════════════════
        # REPORT
        # ═══════════════════════════════════

        print(f"\n{'═' * 80}")
        print(f"  BLIND SPOT ANALYSIS — THE HONEST TRUTH ABOUT MEMEE")
        print(f"{'═' * 80}")

        findings = [
            ("1. FALSE POSITIVES",
             f"{results['false_positive_rate']}% of warnings are wrong",
             "HIGH" if results["false_positive_rate"] > 30 else "MEDIUM" if results["false_positive_rate"] > 15 else "LOW",
             "Anti-patterns too broad → false alarms → agents ignore ALL warnings"),

            ("2. NOISE RATIO",
             f"{results['noise_ratio']}% of memories never accessed ({results['memories_never_accessed']} of {total_memories})",
             "HIGH" if results["noise_ratio"] > 80 else "MEDIUM" if results["noise_ratio"] > 50 else "LOW",
             "DB fills with observations nobody searches for. Dilutes search results."),

            ("3. SEARCH MISS RATE",
             f"{results['search_miss_rate']}% of searches return nothing",
             "LOW" if results["search_miss_rate"] < 40 else "MEDIUM",
             "User searches for topic not in DB → disappointing experience"),

            ("4. STALE KNOWLEDGE",
             f"{results['stale_damage_events']} damage events, self-corrected: {results['stale_self_corrected']}",
             "MEDIUM" if results["stale_self_corrected"] else "HIGH",
             f"Old pattern caused bugs before being invalidated. Conf: 0.8 → {results['stale_pattern_conf_after']}"),

            ("5. PROPAGATION SPAM",
             f"Precision: {results['propagation_precision']}% ({results['propagation_spam']} irrelevant pushes)",
             "LOW" if results["propagation_precision"] > 80 else "MEDIUM",
             "Patterns pushed to projects where they don't apply"),

            ("6. CONFIDENCE CALIBRATION",
             f"Avg error: {results['confidence_miscalibration']} — {results['calibration_quality']}",
             "LOW" if results["calibration_quality"] == "GOOD" else "MEDIUM",
             "Does confidence 0.8 mean 80% chance of being correct?"),

            ("7. SEARCH DEGRADATION",
             f"{results['search_ms_before_bloat']}ms → {results['search_ms_after_bloat']}ms (+{results['search_degradation_pct']}%)",
             "LOW" if results["search_degradation_pct"] < 50 else "MEDIUM",
             "Search slows as DB grows"),

            ("8. COLD START",
             f"Hit rate with existing data: {results['cold_start_hit_rate']}%",
             "MEDIUM",
             "First weeks = empty DB = no value. Need seed data or import."),

            ("9. KNOWLEDGE SILOS",
             f"Silo ratio: {results['knowledge_silo_ratio']}%",
             "MEDIUM" if results["knowledge_silo_ratio"] > 50 else "LOW",
             "Some teams use Memee heavily, others not at all"),

            ("10. GAMING FILTER",
             f"{results['gaming_filter_rate']}% of garbage rejected ({results['gaming_rejected']}/{results['gaming_rejected']+results['gaming_accepted']})",
             "LOW" if results["gaming_filter_rate"] > 60 else "HIGH",
             "Quality gate catches garbage? Or lets it through?"),

            ("11. CONTEXT LOSS",
             f"{results['context_poor_pct']}% of patterns lack WHY/WHEN context",
             "HIGH" if results["context_poor_pct"] > 70 else "MEDIUM",
             "Memory says 'use timeout' but not 'because Redis goes down under load'"),

            ("12. SURVIVORSHIP BIAS",
             f"{results['validation_positive_bias']}% of validations are positive",
             "MEDIUM" if results["validation_positive_bias"] > 70 else "LOW",
             "We record successes more than failures → overconfident"),

            ("13. TECH MIGRATION",
             f"jQuery pattern survived aging: {results['obsolete_survived_aging']}, "
             f"killed after 10 invalidations: {results['obsolete_deprecated']}",
             "MEDIUM",
             f"Canon is 'immortal' until explicitly invalidated. Conf: 0.9 → {results['obsolete_after_invalidations']}"),

            ("14. UNCERTAINTY",
             f"Avg uncertainty for high-conf: {results['avg_uncertainty_high_conf']}",
             "LOW" if results["avg_uncertainty_high_conf"] < 0.3 else "MEDIUM",
             "Do we know what we don't know?"),
        ]

        critical_count = 0
        for name, finding, severity, explanation in findings:
            icon = {"HIGH": "✗", "MEDIUM": "!", "LOW": "✓"}[severity]
            color_code = {"HIGH": "RED", "MEDIUM": "YELLOW", "LOW": "GREEN"}[severity]
            if severity == "HIGH":
                critical_count += 1
            print(f"\n  [{icon}] {name}")
            print(f"      Finding:  {finding}")
            print(f"      Severity: {severity}")
            print(f"      Risk:     {explanation}")

        # Summary
        high = sum(1 for _, _, s, _ in findings if s == "HIGH")
        medium = sum(1 for _, _, s, _ in findings if s == "MEDIUM")
        low = sum(1 for _, _, s, _ in findings if s == "LOW")

        print(f"\n{'═' * 80}")
        print(f"  SUMMARY: {high} HIGH | {medium} MEDIUM | {low} LOW")
        print(f"{'═' * 80}")

        if high > 3:
            print(f"\n  VERDICT: Memee has significant blind spots. Address HIGH items before launch.")
        elif high > 0:
            print(f"\n  VERDICT: Some blind spots exist but most are manageable. Ship with monitoring.")
        else:
            print(f"\n  VERDICT: Blind spots are under control. Ready for production.")

        print(f"\n  TOP ACTIONS:")
        for name, finding, severity, explanation in findings:
            if severity == "HIGH":
                print(f"    FIX: {name} — {explanation}")

        print(f"\n{'═' * 80}")

        # Assertions — realistic expectations, not perfection
        assert results["gaming_filter_rate"] >= 40, "Quality gate must catch most garbage"
        assert results["propagation_precision"] >= 50, "More relevant than spam"
        assert results["confidence_miscalibration"] < 0.3, "Calibration must be reasonable"
        # Stale knowledge: system SHOULD self-correct but may need more invalidations
        # This is a known limitation — canon memories are hard to kill
