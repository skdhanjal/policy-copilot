import yaml
from app.guardrails.injection import detect_injection

attacks = yaml.safe_load(open("redteam/attacks.yaml"))
results = []
for a in attacks:
    blocked = detect_injection(a["text"])
    correct = blocked == a["expect_blocked"]
    results.append((a["id"], a["category"], correct, blocked, a["expect_blocked"]))

print(f"{'id':15}{'category':12}{'result':8}{'blocked':9}{'expected'}")
for id_, cat, correct, blocked, expected in results:
    mark = "PASS" if correct else "FAIL"
    print(f"{id_:15}{cat:12}{mark:8}{str(blocked):9}{expected}")

n_correct = sum(1 for r in results if r[2])
print(f"\n{n_correct}/{len(results)} correct")
