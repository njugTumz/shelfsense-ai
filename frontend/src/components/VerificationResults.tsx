import {
  type AuditFinding,
  type CoverageReport,
  type E2BVerification,
  type VerificationResult,
} from "../services/api.ts";

const RESULT_COPY: Record<string, { title: string; tone: string }> = {
  VERIFIED: { title: "Verified", tone: "text-moss-800" },
  VERIFIED_WITH_WARNINGS: { title: "Verified with warnings", tone: "text-amber-800" },
  FAILED: { title: "Failed", tone: "text-red-800" },
  AUDIT_LIMITED: { title: "Audit limited", tone: "text-amber-800" },
  INCONCLUSIVE: { title: "Inconclusive", tone: "text-stone-600" },
};

function checkGlyph(status: string): string {
  if (status === "pass" || status === "implemented" || status === "supported") return "✓";
  if (status === "fail") return "✕";
  if (status === "partial") return "◐";
  return "○";
}

function statusTone(status: string): string {
  if (status === "pass" || status === "implemented" || status === "supported")
    return "text-moss-800";
  if (status === "fail") return "text-red-800";
  if (status === "partial") return "text-amber-800";
  return "text-stone-500";
}

function E2BResults({ e2b }: { e2b: E2BVerification }) {
  if (!e2b.available) {
    return <p className="mt-2 text-stone-600">E2B unavailable: {e2b.error}</p>;
  }
  return (
    <div className="mt-2">
      <ul className="space-y-1">
        {e2b.checks.map((check) => (
          <li key={check.name} className="text-stone-700">
            <span className={statusTone(check.status)}>{checkGlyph(check.status)}</span>{" "}
            <span className="font-medium">{check.name.replace(/_/g, " ")}</span>
            <span className="text-sm text-stone-500">
              {" "}
              — {check.category}
              {!check.required && check.status !== "pass" ? " (optional)" : ""}
              {check.latency_s > 0 ? ` · ${check.latency_s}s` : ""}
            </span>
            {check.detail !== "" && (
              <span className="block pl-5 text-sm text-stone-600">{check.detail.slice(0, 280)}</span>
            )}
          </li>
        ))}
      </ul>
      {e2b.preview_url !== "" && (
        <p className="mt-3 text-sm text-stone-600">
          Preview served live during verification at{" "}
          <span className="break-all font-mono text-xs">{e2b.preview_url}</span> — the
          sandbox was stopped after the checks, so this URL no longer responds.
        </p>
      )}
      {e2b.browser !== null && e2b.browser.available && (
        <p className="mt-2 text-sm text-stone-600">
          Real browser: {e2b.browser.browser} · {e2b.browser.latency_s}s ·{" "}
          {e2b.browser.steps.filter((s) => s.status === "pass").length}/
          {e2b.browser.steps.length} steps passed
        </p>
      )}
      {e2b.browser !== null &&
        e2b.browser.screenshots.filter((shot) => shot.data_url !== "").length > 0 && (
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {e2b.browser.screenshots
              .filter((shot) => shot.data_url !== "")
              .map((shot) => (
                <figure key={shot.name}>
                  <img src={shot.data_url} alt={`Browser evidence: ${shot.name}`} />
                  <figcaption className="mt-1 text-xs text-stone-500">{shot.name}</figcaption>
                </figure>
              ))}
          </div>
        )}
      {e2b.error !== "" && <p className="mt-2 text-sm text-red-800">{e2b.error}</p>}
    </div>
  );
}

function FindingCard({ finding }: { finding: AuditFinding }) {
  return (
    <li className="border border-stone-200 bg-white p-3 rounded-lg">
      <p className="text-sm font-semibold tracking-wide text-stone-700 uppercase">
        {finding.severity} · {finding.category}
        {finding.file !== "" ? ` · ${finding.file}` : ""}
      </p>
      <p className="mt-1 text-stone-800">{finding.finding}</p>
      {finding.evidence !== "" && (
        <p className="mt-1 text-sm text-stone-600">Evidence: {finding.evidence.slice(0, 400)}</p>
      )}
      {finding.recommendation !== "" && (
        <p className="mt-1 text-sm text-stone-600">
          Recommendation: {finding.recommendation.slice(0, 400)}
        </p>
      )}
    </li>
  );
}

function CoverageResults({ coverage }: { coverage: CoverageReport }) {
  return (
    <div className="mt-2">
      <p className="text-sm font-semibold text-stone-600">Requirements coverage</p>
      <ul className="mt-1 space-y-1">
        {coverage.requirements.map((item) => (
          <li key={item.requirement_id} className="text-sm text-stone-700">
            <span className={statusTone(item.status)}>{checkGlyph(item.status)}</span>{" "}
            <span className="font-medium">{item.requirement_id}</span> — {item.requirement}
            <span className="block pl-5 text-stone-600">
              {item.status.replace(/_/g, " ")}
              {item.detail !== "" ? `: ${item.detail.slice(0, 220)}` : ""}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm font-semibold text-stone-600">Acceptance criteria</p>
      <ul className="mt-1 space-y-1">
        {coverage.acceptance.map((item, index) => (
          <li key={`${item.criterion.slice(0, 40)}-${index}`} className="text-sm text-stone-700">
            <span className={statusTone(item.status)}>{checkGlyph(item.status)}</span>{" "}
            {item.criterion}
            <span className="block pl-5 text-stone-600">
              {item.status}
              {item.by_checks.length > 0 ? ` — executed: ${item.by_checks.join(", ")}` : ""}
              {item.detail !== "" && item.by_checks.length === 0 ? ` — ${item.detail}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function VerificationResults({ result }: { result: VerificationResult }) {
  const copy = RESULT_COPY[result.status] ?? { title: result.status, tone: "text-stone-800" };
  const blocking = result.audit.findings.filter(
    (f) => f.severity === "critical" || f.severity === "high",
  ).length;
  return (
    <div className="mt-4">
      <h3 className="text-lg font-semibold">
        Result: <span className={copy.tone}>{copy.title}</span>
      </h3>
      {result.status === "INCONCLUSIVE" && (
        <p className="mt-2 text-stone-600">
          Verification could not complete: the E2B sandbox infrastructure failed
          after {result.e2b.transport_retries} automatic{" "}
          {result.e2b.transport_retries === 1 ? "retry" : "retries"}. The app was
          not proven broken — re-run verification.
        </p>
      )}

      <h4 className="mt-4 font-semibold">E2B runtime verification</h4>
      <E2BResults e2b={result.e2b} />

      {result.coverage !== null && (
        <>
          <h4 className="mt-4 font-semibold">Requirements &amp; acceptance coverage</h4>
          <CoverageResults coverage={result.coverage} />
        </>
      )}

      <h4 className="mt-4 font-semibold">Adversarial Muse Spark audit</h4>
      {!result.audit.completed ? (
        <p className="mt-2 text-stone-600">
          Independent AI audit unavailable: {result.audit.error || "no result recorded."}
        </p>
      ) : (
        <div className="mt-2">
          <p className="text-stone-700">
            Verdict: <span className="font-medium">{result.audit.verdict}</span> ·{" "}
            {result.audit.findings.length} finding(s)
            {blocking > 0 ? `, ${blocking} blocking` : ", none blocking"} · model{" "}
            <span className="font-mono text-sm">{result.audit.model_id}</span>
          </p>
          {result.audit.summary !== "" && (
            <p className="mt-1 text-stone-600">{result.audit.summary}</p>
          )}
          {result.audit.findings.length > 0 && (
            <ul className="mt-3 space-y-2">
              {result.audit.findings.map((finding, index) => (
                <FindingCard key={`${finding.finding.slice(0, 40)}-${index}`} finding={finding} />
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="mt-4 text-sm text-stone-500">
        Target auditor Muse Glimmer: not verified — the Modal GPU/account limit
        prevented a genuine inference, so it is not part of this result.
      </p>
    </div>
  );
}
