# Release contract and recovery

`.github/workflows/release.yml` runs on `pull_request_target` **closed/merged**
events for main. The merged-only guard and exact base-repository merge SHA are
mandatory: an unmerged fork head is never checked out with publishing permissions.
Checkout's explicit opt-in is used with frozen input commits. Compiler and
nonpublishing validation jobs have read-only contents permissions and no publishing
secrets. Action SHAs are pinned; the supported release path fixes native hosted
labels. Both release and intentional validation use the [same staged candidate
workflow](STAGED-BUILD.md), whose hosted acceptance remains NOT_RUN.

Release runs are serialized with `queue: max`, retaining up to 100 pending runs
instead of replacing a previous pending merge. GitHub's queue limit still applies;
cancelled runs can be retried for the same eligible merge. The pinned actionlint
does not yet understand `queue` or `cache-mode`; independent workflow validators
check queue/cancellation policy, literal cache access, reusable caller caps and
duplicate keys, suppressing only those unsupported-key diagnostics. All other actionlint checks remain enabled.

リリースは`queue: max`で直列実行し、GitHubの上限である100件まで待機できます。
待機中のマージを次のマージで置き換えません。取り消された実行は同じ適格マージで再実行します。
固定版actionlintが未対応の`queue`と`cache-mode`は別途検証し、他のlint検証は維持します。

The workflow resolves the commit/version and both runners once, reserves an
unpublished draft, builds native ISO/QCOW2/VMDK/source candidates, stages complete
per-architecture sets, validates the aggregate, uploads its Actions artifact, and
only then promotes the draft. A missing/failed/skipped required stage blocks the
publication transition. `producer.actions.promote` requires the uploaded
artifact ID/digest, current run ID, exact version/source/attempt-derived name and
non-expired metadata. The PR event head may differ from the merged build source;
exact clean checkout identity and full remote validation still bind that source.

[v2026.03.8](https://github.com/michibiki-io/shirube-os/releases/tag/v2026.03.8)
was published on 2026-11-12. The latest retained native cohorts
have corresponding-source restoration, component clearance and guest-lifecycle
receipts; exact candidate results and remaining failures are recorded in
[PR #2](https://github.com/michibiki-io/shirube-os/pull/2). Historical component
reports are scoped to their recorded inputs, not a reason to repeat completed
checks. Neither those receipts nor `license-audit.json` alone replace the final
aggregate validator. Failed or missing required evidence still blocks publication.

For unpublished validation, retained binaries and sources keep their original
source commit and signing cohort. A later verifier-only change is recorded as a
separate runtime-verifier/review head; it never relabels the retained source set
as a new build. Production publication still requires the exact eligible merged
commit and the complete coherent candidate contract below.

`Test` runs only on pull-request `opened`, `synchronize`, `reopened` and
`ready_for_review` events; new PR commits use `synchronize`. Direct main/other
branch pushes, tags, closed PRs, manual dispatch and `merge_group` do not start
Test. Job/check names and runners are unchanged. Release `resolve` still validates
the exact merged source before expensive builds. Test success does not establish
image acceptance. The native trial remains explicitly dispatched on feature
branches with its existing cache behavior.

The existing eligible, non-no-op merged Release automatically attempts to populate
shared main caches: public compiler layers and unsigned `native`, `vpp`, `kea`,
`salt` and `runtime` payloads. No separate full-build priming run is required.
Release defaults to GitHub `cache-mode: read`; its gated build caller explicitly
uses literal `write`. The six cache jobs inherit that effective mode and caller
cap. Admission and all other staged jobs explicitly use `read`. Inline admission
independently gates every compiler export and stage save after checking the
original repository, main ref, closed/merged main PR and exact event merge SHA.
A queued merge SHA may differ from `github.sha` or the later main tip.
PR validation, fork-repository dispatch and feature dispatch use a read caller.
Optional exact-main manual validation uses a separate, mutually exclusive write
caller only in `michibiki-io/shirube-os`; its resolver checks source equals
`github.sha` before repository Python. See [cache contracts](STAGED-BUILD.md#automatic-main-cache-population-and-optional-validation).

GitHub keyword `cache-mode` (`read`/`write`) controls service access, separately
from `inputs.cache-mode` (`cold`/`warm`), which selects intentional prior-cache use.
Stage saves require success and no exact outer hit; optional compiler export uses
`ignore-error=true`. Buildx keeps `cache-binary: false` to prevent implicit binary
post-saves. Cache-v2 keys and inner inventory/integrity/provenance checks remain
unchanged. Kernel signing secrets/state, clearance and guest
success remain fresh and outside shared caches. Actual runner saves and the next
warm hit remain **NOT_RUN**; fixtures and optional exports prove neither.
Hosted acceptance of the new Release/validation caller configuration is also
**NOT_RUN**: Test does not execute those workflows. On the first ordinary
post-merge Release, inspect workflow startup and the expected write-capable
cache-mode warning before treating its cache logs as evidence.

Approval to merge into main authorizes executing the merged build recipes with
cache credentials. Those recipes and **all shared-main cache writers** must be
trusted. Hashes preserve integrity/provenance; they cannot protect against a
malicious trusted writer. Cache eviction may still require fresh compilation.

Existing compiler artifacts remain bound to the original source commit and run.
Same-run recovery revalidates those bindings; it cannot establish acceptance of
a new commit. Rerunning an old run uses its old workflow/code and does not apply
this fix. Do not relabel or substitute artifacts across runs.

The 2026-11-12 rule-inspection snapshot records
[main ruleset 26762240](https://github.com/michibiki-io/shirube-os/rules/26762240):
one approving PR review (with stale-review dismissal, Code Owner review and
resolved review threads), deletion protection and non-fast-forward protection. No required-status-check or merge-queue
rule was returned.
This snapshot is not a guarantee of future rules, and no repository settings
were changed. If status-check or merge-queue requirements change,
reassess the PR-only event mapping; Test currently has no `merge_group` coverage.

`Test`はPRの`opened`、`synchronize`、`reopened`、`ready_for_review`だけで起動します。
PRへの追加commitは`synchronize`で検証します。mainや他ブランチへの直接push、タグ、
PRのclose、手動実行、`merge_group`では起動しません。ジョブ名とrunnerは維持します。
リリースの`resolve`は、ビルド前に正確なマージ済みソースを引き続き検証します。
Testの成功だけではイメージの合格を意味しません。native trialはfeature branchでの
明示的な手動実行と既存のキャッシュ動作を維持します。

既存のReleaseは、適格かつno-opでないマージ後の実行で、公開コンパイラーと
署名前の`native`、`vpp`、`kea`、`salt`、`runtime`をmainの共有キャッシュに自動保存します。
別の全工程ビルドによる初期化は不要です。
ReleaseのGitHub `cache-mode`は既定で`read`とし、既存ゲートを通るbuild呼び出しだけを
リテラルの`write`にします。保存する6ジョブは呼び出し元の実効モードと上限を継承し、
admissionと他のステージジョブは明示的に`read`を指定します。
inline admissionは元リポジトリ、main ref、mainへのclosedかつmergedなPR、
イベントの正確なmerge SHAを検証し、すべてのexportとsaveを個別に制御します。
待機中のmerge SHAは`github.sha`や後続のmain先端と異なっていても構いません。
PR、forkリポジトリ、feature dispatchはreadで呼び出します。
任意の手動検証は元リポジトリのmain dispatchだけを排他的なwrite経路にし、
リポジトリのPython実行前にsourceと`github.sha`の一致を確認します。

GitHubの`cache-mode`（read/write）はサービス側のアクセス制御で、
既存キャッシュを使うか選ぶ`inputs.cache-mode`（cold/warm）とは別です。
ステージは成功し、正確な外側キーのヒットがない場合だけ保存します。
コンパイラーのexportは`ignore-error=true`による任意の試行です。
Buildxの`cache-binary: false`、cache-v2のキー、内容と来歴の検証は維持します。
kernelの署名用秘密鍵と状態、clearance、guest成功結果は共有キャッシュに入れず、毎回検証します。
実runnerでの保存成功と次回のwarm hitは**NOT_RUN**で、fixtureや任意のexportでは実証できません。
新しいRelease・検証caller構成のGitHub上での受理も**NOT_RUN**です。Testはそれらを実行しません。
最初の通常のマージ後Releaseでworkflowの起動と、write指定に伴う警告を確認してから、
キャッシュログを保存の証拠として扱います。

mainへのマージ承認は、キャッシュ認証情報を持つビルドレシピの実行許可になります。
承認済みレシピと、mainの共有キャッシュに書き込むすべてのwriterを信頼する必要があります。
ハッシュは整合性と来歴を検証しますが、悪意ある信頼済みwriterからは保護できません。
キャッシュの削除や欠落によって再コンパイルが必要になる場合もあります。

既存のコンパイラー成果物は元のソースcommitとrunに結び付いたままです。
同じrunの復旧でもこの対応を再検証し、新しいcommitの合格根拠にはできません。
古いrunの再実行は古いworkflowとコードを使うため、この修正は適用されません。
別runの成果物への付け替えや、ラベルを変更した流用はしません。

2026-11-12の確認記録では、[mainのruleset 26762240](https://github.com/michibiki-io/shirube-os/rules/26762240)に
1件のPR承認、追加push時の承認失効、Code Ownerレビュー、レビュースレッドの解決、削除禁止、non-fast-forward禁止が
あります。required status checkとmerge queueのルールは返されていません。この記録は将来のルールを保証せず、設定も変更していません。
status checkやmerge queueの要件が変わる場合は、PR専用の起動条件を再確認します。
現状のTestは`merge_group`を検証しません。

## Merged-change impact

English

Publication and validation are independent decisions in `producer/release_plan.py`
(policy schema 1). The publication values are `publish`, `none`, and `hold`;
validation is `focused` or `candidate`. Classification uses semantic consumer roles,
not PR titles, labels, or whether a compiler edit might produce identical bytes.
A build improvement may change bytes while remaining validation-only.

| Semantic change | Publication | Required verification |
| --- | --- | --- |
| Product behavior/configuration, pinned sources, patches, legal material | publish | Both native architectures, current candidate, aggregate and all existing publication gates |
| Compiler/toolchain/build/package/cache infrastructure, including #37 | none | Same current dual-architecture candidate and aggregate; no Release upload |
| Publication/orchestration control | none | Focused orchestration/contract tests; candidate/guest skipped-not-required |
| CI-only workflow checks | none | Focused checks and ordinary unfiltered PR CI |
| Explanatory docs and root unittest modules/helpers | none | Ordinary PR CI; no new guest PASS |
| Artifact/source/security/guest acceptance contracts, schemas, fixtures/evidence | none unless product also changes | Current candidate and aggregate under the changed contract |
| Known product mixed with infrastructure | publish | Union of required gates |
| Unknown role, including either rename side or a deleted path | hold | Conservative current candidate and aggregate; no version reservation |
| Invalid inventory/untrusted event | blocked/rejected | No allocation or build |
| Failure/cancellation | no publication | Required failed/unrun remains visible; no cleanup of a product reservation |

Product paths include `guest/`, `patches/`, `third_party/`, `LICENSES/`, source and
package-selection locks, product branding/configuration generators and the native
image definition. Build roles include compiler/native/stage/component recipe
families, containers, build scripts, artifact workflows and context controls.
Publication controllers (`actions.py`, `release_plan.py`, and the release workflow)
are focused control inputs. Root `tests/test_*.py` and `tests/*_fixture.py` are
unittest families; explanatory `README.md`/`docs/**/*.md` are docs families.
`tests/fixtures/`, `docs/evidence/`, schemas and examples are acceptance inputs,
never suffix-exempt. Explicit compile declaration consumers override docs/test
families. New unknown producer modules/root files hold rather than mint a version.
The exact role table and ordered matching rules are executable in the policy.

Extension contract: identify the installed product, build, validation or control
consumer first; update the semantic role/dependency declaration and add positive,
negative and consumer-mutation regressions. A new executable consumer of a doc or
fixture must declare that input. Inspect Docker copies, compile input declarations,
imports, shell/workflow consumers and the source/security contract. Do not add an
issue-number exemption or silently treat unfamiliar input as harmless. Product
roles precede consumer overrides; consumed docs/tests precede their ordinary roles.
Changing classification alone is control work, not a product-release request.

The resolver retains complete paginated file inventory validation: positive integer
count, exact unique records, well-formed status and relative paths, rename origins
and both sides, additions and deletions. It admits only a closed/merged main PR
with a full merge SHA, then verifies the exact clean merged checkout even for a
nonpublication decision. Classification precedes release lookup, allocation and
preflight. `none`/current-diff `hold` never query releases or allocate versions.

A product-triggered run may read the latest published ancestor and classify **all
intervening commits** before allocation. An unresolved cumulative unknown changes
publication to hold. A taxonomy-only merge does not publish a held product;
deferred content can ship in a later product-triggered, fully classified release.
Notes include the full published-to-current history, commit messages/SHA and role
reasons, plus original current-PR messages absent after squash/rebase. Initial
publication covers reachable history. Reserved draft reruns retain ownership;
published-product reruns only identify and fully verify their own exact existing
publication, with no new build, allocation or mutation.

Candidate-only runs use fixed `v<stream>.0` **without release lookup/reservation**.
The number may equal a public version; source commit/run/attempt, candidate-only
plan identity and the forbidden Release mutation paths distinguish the candidate.
Read-only candidate jobs use the same staged workflow and same-run immutable
compiler/architecture artifacts. Both candidates are downloaded and individually
validated, then the existing shared-provenance/combined manifest/checksum/full
validator runs locally. No release staging, asset upload or promotion occurs.
Every mutation entry also requires a product decision and rechecks the event's
product inventory and exact source; a candidate version string is not authority.

Only the original product `build` caller retains the write cache cap and release
purpose. Validation-only uses `read` and `purpose: validation`; inline admission
withholds cache-writer status. Existing main dispatch behavior is unchanged.
The kernel importer still recognizes only the original successful product `build`
job provenance, not the new validation job. Content scopes, artifact digests,
architecture/source identity, fresh kernel keys/signing, source clearance and
current guest checks are unchanged. No signed kernel or old guest PASS is reused.
Changed compiler or acceptance dependencies justify a current candidate; unchanged
control/docs/test changes never enter the expensive kernel/guest path. Prior
published evidence proves only its recorded source/version/bytes/contracts.
Compile caches can assist work only through their existing trust/content gates;
a hit never proves binary equality or guest acceptance.

`release-plan-<attempt>` records source/run/attempt, policy SHA-256, roles, Git
before/after blob+mode identities, required scopes, invalidation/non-reuse reasons
and required-but-unrun gates before candidate execution. `release-evidence-<attempt>`
and the final job summary record `passed`, `failed`, `required-but-unrun`, or
`skipped-not-required`. A skipped publish cannot hide a required validation failure.
If cancellation prevents the final job from running, the initial plan remains
unrun evidence and the Actions cancellation is authoritative; it is never PASS.
A product candidate reserves its owned draft before compilation and publishes only
after both architectures, aggregate, remote assets and verified Actions upload.
Failure/cancellation retains that draft; later product allocation skips reserved
versions. Candidate-only failure leaves Actions evidence without a reservation.
Existing .12, empty .13 draft and cancelled run 43462814939 are not modified.

The commit-wide `git archive` still includes docs/tests in `project-source.tar`.
They reach published corresponding sources in the next product release, without
rewriting prior published bytes. This change itself is control/build validation
only: it does not request an official Release on merge. Its staged-workflow change
requires candidate validation after a human merge; that hosted acceptance and
actual product Release acceptance are **NOT_RUN** during implementation.

日本語

**公開判定**（publish/none/hold）と**検証 scope**（focused/candidate）を別々に決めます。
製品の動作、設定、固定 source、patch、法的入力は製品公開の対象です。
compiler、toolchain、build、cache の変更は、バイナリが変わる可能性があっても検証専用です。
公開制御と CI の変更には限定した契約試験を要求し、文書と単体試験だけの変更には通常の PR CI を行います。
受入契約、fixture、schema、証拠資料は候補検証の対象で、文書や test の拡張子だけでは除外しません。
未知、削除、rename 両側を分類し、未知が製品と混ざる場合も公開を保留します。

分類の拡張では、製品、build、受入、制御の消費先を調べ、意味上の規則と依存宣言を更新します。
肯定例、反例、消費先追加の mutation test を追加します。
新しい test や説明文書を1件ずつ除外リストへ登録する必要はありません。
実行入力として宣言された文書や試験ファイルは、通常の文書や単体試験より優先して候補検証の対象にします。
分類規則だけの変更では製品公開を行いません。

全ページの変更一覧と件数、一意性、status、相対パス、rename 元を検証してから分類します。
信頼済みの merged-main イベントと正確で clean な merged checkout を確認します。
現行差分が none/hold の場合は Release を参照せず、版、tag、draft を割り当てません。
製品公開の契機では前回公開以降の全 commit を再分類し、未知が残れば割当前に保留します。
保留内容は、後続の製品公開で分類されるまで繰り延べます。
notes は途中の基盤変更や保留変更も含む全範囲と分類理由、現行 PR の元メッセージを記録します。
公開済み製品 commit の再実行だけは、所有する版と全資産を読み取り検証して終了します。

検証専用候補は Release を参照せず固定の Stream 互換 `.0` を帰属表示に使います。
数字は公開版と一致しえますが、commit/run/attempt と候補専用記録、公開経路の禁止で区別します。
同一 run の両 architecture の候補を個別検証してからローカルで集約し、既存の provenance、manifest、checksum、両 architecture の validator を通します。
Release への staging、upload、promotion は行いません。
公開関数も製品判定とイベントの製品入力、正確な source を再確認します。

検証専用 caller の cache 権限は read で、inline admission は writer にしません。
従来の製品 build と main dispatch の信頼境界を保持します。
compiler 入力や受入契約の変更には現行候補を要求し、新規署名と現行 guest 検証を維持します。
制御、文書、単体試験だけの変更では高価な kernel/guest 作業を行いません。
cache hit や過去版の PASS を現行成果物の同一性や合格として扱いません。

plan artifact は実行前の required-but-unrun と source/run/attempt、policy hash、変更前後の Git object/mode、分類と無効化理由を記録します。
最終 evidence と job summary は passed、failed、required-but-unrun、skipped-not-required を区別します。
publish が skip しても、必須の候補検証が失敗した run は成功になりません。
取消により最終記録が走らない場合も、初期 plan は未実施の証拠として残り、取消を PASS としません。
製品候補の失敗や取消では所有 draft を保持し、検証専用では予約自体を作りません。
既存の公開済み .12、空の .13 draft、取消済み run は変更しません。

tracked の文書と試験は引き続き project-source.tar に入り、次の製品公開で対応 source に含まれます。
今回の差分は制御と build 基盤で、merge による正式 Release の新規作成は要求しません。
staged workflow の変更に対する hosted candidate 検証は人間の merge 後に必要です。
その実行と製品 Release の実受入は、実装時点では NOT_RUN です。

| AC | Focused evidence / 限定した証拠 |
| --- | --- |
| AC1 | `TaxonomyTests`: semantic matrix, new families, consumers, #37 infrastructure and v.10 product regression, rename/deletion |
| AC2 | `ResolutionTests`, retained `ReleaseImpactTests`: no release lookup/allocation for none/hold/retry; strict malformed inventory rejection and event guards |
| AC3 | `TaxonomyTests` status matrix, `CandidateAggregateTests`, workflow admission/dependency tests: skip is not PASS, failed/unrun required work fails |
| AC4 | Same-run individual/aggregate validation and rejection fixtures; existing release, artifact, source, cache/signing tests remain unchanged in scope |
| AC5 | Retained reservation/rerun/supersession/commit-pagination tests and `HistoryTests`; cumulative hold and full notes history |
| AC6 | Actual own diff classification, focused suites and workflow checks, exact-head Test CI and independent reviews; hosted candidate/Release NOT_RUN |

## Version allocation

Release versions follow `v<Stream YYYY.MM>.<Shirube revision>`. With pinned
Stream `2026.03`, the first release is `v2026.03.0`; each new eligible merged
commit on that baseline advances the revision (`v2026.03.1`, `v2026.03.2`, ...).
Updating the pinned baseline to `2026.04` starts `v2026.04.0`. A baseline downgrade
is rejected. The month keeps its leading zero; this is a Stream-aligned version
scheme, not strict SemVer. Tags, release titles, ISO/disk/source names, Shirube integration-package
versions, manifests and OS version output use the same resolved value. Third-party
component versions remain at their exact pinned upstream values.

After selecting the actual release version, `resolve` runs a local, network-free
integration approval preflight for both amd64 and arm64 before emitting the
resolved commit/version. Runner matrices and stage deadlines are defined in the
workflows; resolve emits no runner matrix. Fresh releases run it before creating
their draft; unfinished-draft retries check their reserved version again. Failure stops resolution without
starting builds or creating a new draft. Published releases keep their existing
complete-asset verification and no-op path. Eligibility, reservation ownership,
supersession and publication gates remain in force. The preflight uses shared
integration identity/notice inputs and the bounded same-Stream revision policy
in [LICENSES.md](LICENSES.md); its retained-notice input does not verify freshly
downloaded bytes or replace final source, dependency, guest or publication checks.

`resolve` は実際のリリース版を決定後、commitとversionを出力する前に amd64 と arm64 の
integration 承認をローカルで確認します。ネットワークは使用しません。
runnerの行列と各ステージの制限時間はworkflowで定義し、resolveは行列を出力しません。
新規版は draft 作成前、未完了 draft の再試行は予約済みの実版で確認し、
失敗時はビルド開始と新規 draft 作成を防ぎます。
公開済みリリースでは従来どおり全成果物を検証し、再ビルドせず終了します。
適格性、予約の所有権、後続版による失効、公開の条件は維持します。
識別情報と通知文の入力を共用し、[LICENSES.md](LICENSES.md) の同一 Stream 内に限る
方針を適用します。保持通知を使う事前確認は、新たな取得物の内容や最終ソース検証、
依存の確認、guest 試験、公開時の検証を代替しません。

Unpublished drafts reserve their version even if their build or validation fails.
A later eligible commit skips versions reserved by other commits and receives the
next revision. Rerunning the same commit keeps its own reserved version unless
a higher normal (non-prerelease) version has already been published: `resolve`
rejects that superseded unpublished draft rather than publishing an older version
after its successor. `publish` checks again before changing aggregate assets, and
`promote` checks both before validation and immediately before publication. These
checks also cover **Re-run failed jobs**, which can reuse a successful `resolve`
job's old outputs without running that job again. The evidence job downloads the
plan artifact named by those resolve outputs, not the current attempt number.
Its result records `plan_run_attempt` and `evidence_run_attempt` separately.
部分再実行でも resolve 出力が指す元の plan を読み、plan の生成 attempt と
evidence の生成 attempt を別々に記録します。
This does not change verification-only reruns of an already published release.
Only published releases establish the release ancestry check. Draft reservations
affect version allocation and baseline-downgrade checks; they do not establish
ancestry or waive any publication gate.

A draft reserved on a newer Stream baseline also blocks allocation after a
return to an older baseline, even while that draft remains unpublished. This is
intentional fail-closed behavior. Recovery requires a person to verify that the
reservation is still unpublished and not in use by another run before deciding
whether to discard it. Published releases and their assets remain immutable;
this recovery does not permit deleting or changing a published release.

Conventional Commit subjects/bodies and SHAs form release notes, including
breaking-change notices. Their type does not change Stream's year/month. PR
titles do not determine versions or substitute for individual commits. GitHub
commit metadata is checked for complete coverage. The exact eligible merged
commit/version is resolved once and shared by both architectures; permitted
reruns keep the reserved version, subject to the superseded-draft rejection above
and the published-asset verification rules below.

`producer.release.validate` validates the metadata contract against actual files:
identity, complete architecture/role sets, physical sizes, hashes, checksum
coverage, candidate generations, required check outcomes, detailed evidence and
exact source/license members. Every required result must be `PASS`; `SKIP`,
`NOT_RUN`, missing checks and inconsistent evidence fail. This validator does not
execute a VM: it consumes evidence from a trusted producer. Synthetic validator
fixtures are explicitly marked and are not runtime acceptance evidence.

Candidate generations bind each architecture's ISO/disks/source/evidence to the
same build-internal signing keys. Before publication, reuse only a complete
verified candidate; otherwise rebuild the whole architecture candidate and
regenerate aggregate metadata in unpublished staging. Never mix generations.

Publish only after every required gate passes and the entire asset set exists in
an unpublished draft. Every source component archive must be below the GitHub
2 GiB per-asset limit. Hash every asset plus `manifest.json` in `SHA256SUMS`;
checksums do not include themselves. Verify all source archives and their member
hashes. Retain source archives with the binaries in the same release.

GitHub permits at most 1000 assets per release, including the manifest and
checksum list ([release quotas](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)).
Debian source components are grouped into plain-tar parts of at most 16
components, with a conservative 1 GiB input budget per part. Their original
source archives, copyright files and recipes remain byte-identical under separate
component directories. The inventory gives each exact part/member/hash reference;
the normal source restoration command handles these parts directly. Custom
component archives retain their existing format. Each new part is independently
re-read before original intermediate component archives are removed. Aggregate
validation counts the manifest/checksum assets, and draft staging rejects an
over-limit set before deleting or uploading any candidate material.

GitHub Releaseはmanifestとchecksumを含めて最大1000アセットです。
Debianソースは最大16コンポーネントごとの非圧縮tarにまとめ、各partの入力を
余裕を持って1 GiB以内に制限します。原本アーカイブ、著作権表示、ビルド手順の
内容を変更せず、別々のディレクトリーへ収録します。inventoryにpart名、member名、
ハッシュを記録し、通常のソース復元コマンドで復元できます。新しいpartの全memberを
再検証してから中間アーカイブを削除します。合計数が上限を超える場合、候補の削除や
アップロードを始める前に停止します。

After publication, reruns must download and verify the complete recorded set,
including metadata, before doing any build work. An exact match is a verification-
only no-op. Missing or changed published material fails without any upload,
replacement, metadata rewrite or repair. Recover with a new eligible merged
change and superseding version. Reconcile uncertain publication outcomes against
live release state before retrying. Draft staging and final promotion are implemented in `producer.actions`.
They recheck draft ownership before writes and reconcile uncertain final
publication against live release state without rewriting a published set.

OCI status is independently `NOT_RUN`. The current validator refuses a positive
OCI claim because live-evidence validation is not implemented. Local QEMU boot
success cannot establish live OCI compatibility.

The skill-based release baseline was rendered and inspected before adaptation.
This OS producer intentionally does not publish builder containers to a registry,
use QEMU for cross-architecture compilation, or require Helm/cloud deployment.
Relevant merged PRs are selected by actual changed paths rather than PR-title
formatting. `scripts/release/compute_release.py` and
`scripts/release/generate_release_notes.py` expose the same canonical policy for
local inspection.

Release manifests and per-architecture evidence bind the explicitly selected
`qemu_accelerator` (`kvm` or validated `tcg`). A missing or differing value fails
validation. Architecture-specific virtualization/runtime/resource variables are
documented in [BUILD.md](BUILD.md); resource or virtualization failure does not
choose another runner or publish a partial result. Policy accepts explicit TCG
for both architectures, and the release workflow defaults to TCG for both hosted
runner classes. This is not runtime acceptance: historical ARM64 TCG component
passes and the retained ARM64 native-KVM candidate are distinct evidence.
Complete hosted-candidate resource/time and runtime acceptance remain separately
gated; see [BUILD.md](BUILD.md#explicit-release-virtualization-and-container-runtime)
and [retained candidate evidence](VALIDATION.md#retained-candidate-evidence-2026-11-11).

manifestと検証記録の仮想化方式も照合します。不足・不一致は公開を拒否し、資源や
仮想化の不足を別runnerへの自動切替で隠しません。policyは両アーキテクチャの明示的な
TCGを受け付け、hosted runnerの既定値もTCGです。ただし設定の受入れは実行試験の
合格ではありません。過去のARM64 TCGコンポーネント試験と、保持候補のnative KVM
試験は区別し、hosted候補全体の容量・時間・ランタイムの合格は別途必要です。

## Fixed unsigned artifact migration window

Normal trusted warm Release misses may use the fixed successful unsigned stages
from run `43164071342` until `2026-11-19T05:50:46Z`. The build caller alone adds
`actions: read`, inherited only by the five importing jobs; PR/manual callers
retain contents-only permissions. Full bounds, provenance, failure behavior and
NOT_RUN hosted evidence are in the [fallback contract](STAGED-BUILD.md#bounded-original-release-artifact-fallback).
No source Release operation or separate full build is part of this migration.

通常の信頼済みwarm Releaseのmissでは、`2026-11-19T05:50:46Z`まで固定runの成功した署名前stageを取り込めます。
build callerにだけ`actions: read`を追加し、対象5jobが継承します。
PRと手動callerの権限は広げません。
制限、来歴、失敗時の動作、NOT_RUNの実環境証拠は上記contractを参照してください。
元Releaseの操作や別の全工程ビルドは行いません。

## Completed v2026.03.8 recovery (Issues #24 and #26)

English

[v2026.03.8](https://github.com/michibiki-io/shirube-os/releases/tag/v2026.03.8)
was published at `2026-11-12T13:48:33Z` (`draft=false`, `prerelease=false`).
[Recovery run 43229875239](https://github.com/michibiki-io/shirube-os/actions/runs/43229875239)
passed in 6m 1s. Original source/tag
`4498c6104b65dfae37be1e5ebe8d78c27100884b` remained distinct from controller
`937431ecac06efcc8198535a1dbc008f8f08808c`.

The original run was `43164071342`, attempt `1`, artifact `13941132568`.
Its GitHub upload digest was
`783c13ef2ef1a80c42ec66c561fe2c59bdc014cec1296031213d53731d34d0e2`;
this is upload evidence, not a fresh ZIP-byte hash.
The 182 existing assets totaling 19,162,268,023 bytes were downloaded once and
validated with the original source's validator, without rebuilding or re-uploading.
The published bytes remained unchanged. Metadata SHA256 values were:

- `manifest.json`: `326b10ea6b13bcf83d4a9f6845cdc7b8505b6584881dc0d2cb2b60dcdb438745`
- `SHA256SUMS`: `11d0bb47006a6ff6a7f6ffcff49319dd4c89941ece35560765bcf7548d26e874`

Issue #26 retires the one-time workflow, module and dedicated tests; their
implementation remains in Git history. Normal artifact/run/source binding,
ownership/inventory checks and promotion regressions remain in place.
Historically, Issue #26 used five exact publication exclusions. The semantic
policy above now classifies those controller/test/deletion paths before release
lookup or allocation, while also verifying checkout identity. The three
retired entries (`.github/workflows/recover-release.yml`,
`producer/recover_release.py`, `tests/test_recover_release.py`) remain because
removed files are included in the deletion PR's complete changed-file inventory.
Together with `producer/actions.py` and `tests/test_actions.py`, these exact
exemptions keep that PR non-image after merge. Both rename origins and destinations are classified; unknown roles hold
publication and require candidate validation, while known mixed product changes publish.
This completion does not change unrelated historical hosted, OCI or cache evidence.

日本語

[v2026.03.8](https://github.com/michibiki-io/shirube-os/releases/tag/v2026.03.8)は
`2026-11-12T13:48:33Z`に公開済みです（`draft=false`、`prerelease=false`）。
[復旧run 43229875239](https://github.com/michibiki-io/shirube-os/actions/runs/43229875239)は6分1秒で成功しました。
元ソースとtagは`4498c6104b65dfae37be1e5ebe8d78c27100884b`、
controllerは`937431ecac06efcc8198535a1dbc008f8f08808c`で、両者は別々のcommitのままです。

元runは`43164071342`のattempt `1`、artifactは`13941132568`です。
GitHub upload digestは
`783c13ef2ef1a80c42ec66c561fe2c59bdc014cec1296031213d53731d34d0e2`です。
これはuploadの証拠であり、新たにZIPのbytesを取得してhashしたという記録ではありません。
既存の182 assets（合計19,162,268,023 bytes）を一度だけ取得し、元ソースの検証器で検証しました。
再ビルドや再uploadはなく、公開したbytesも変更していません。
metadataのSHA256は次のとおりです。

- `manifest.json`: `326b10ea6b13bcf83d4a9f6845cdc7b8505b6584881dc0d2cb2b60dcdb438745`
- `SHA256SUMS`: `11d0bb47006a6ff6a7f6ffcff49319dd4c89941ece35560765bcf7548d26e874`

Issue #26で一回限りのworkflow、module、専用テストを削除し、実装はGit履歴に残します。
通常公開のartifact、run、sourceの対応検証、所有権と一覧の検証、公開処理の回帰テストは保持します。
Issue #26 当時の5つの完全一致除外は、現在は上記の意味上の制御、試験、削除の分類で扱います。
checkout identity は非公開経路でも確認し、Release 参照と割当前に分類します。
削除した3パス（`.github/workflows/recover-release.yml`、`producer/recover_release.py`、
`tests/test_recover_release.py`）も削除PRの全変更一覧に含まれるため、除外に残します。
`producer/actions.py`、`tests/test_actions.py`と合わせて、削除PRをmerge後もイメージ対象外に保ちます。
rename元とrename先の両方を判定し、未知は公開を保留して候補検証を要求します。
既知の製品入力との混合変更は製品公開の対象です。
今回の完了によって、無関係な過去のhosted、OCI、cacheの検証記録は変わりません。
