import yaml
from app.guardrails.injection import detect_injection

attacks = yaml.safe_load(open("redteam/attacks.yaml"))
results = []
for a in attacks:
    # indirect = simulated chunk text, tests same detect_injection() since
    # that's what the ingest scan also uses -- same function, different
    # framing of what's being checked (chunk text vs user query)
    blocked = detect_injection(a["text"])
    correct = blocked == a["expect_blocked"]
    results.append((a["id"], a["category"], correct, blocked, a["expect_blocked"]))

by_cat = {}
for id_, cat, correct, blocked, expected in results:
    by_cat.setdefault(cat, []).append(correct)

print(f"{'id':15}{'category':12}{'result':8}{'blocked':9}{'expected'}")
for id_, cat, correct, blocked, expected in results:
    mark = "PASS" if correct else "FAIL"
    print(f"{id_:15}{cat:12}{mark:8}{str(blocked):9}{expected}")

print()
for cat, vals in by_cat.items():
    print(f"{cat:12} {sum(vals)}/{len(vals)}")

n_correct = sum(1 for r in results if r[2])
print(f"\nTOTAL: {n_correct}/{len(results)}")
