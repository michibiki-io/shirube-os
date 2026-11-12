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
does not yet understand `queue`; the workflow checker validates its values,
cancellation policy and duplicate keys separately, suppressing only that known
unsupported-key diagnostic. All other actionlint checks remain enabled.

リリースは`queue: max`で直列実行し、GitHubの上限である100件まで待機できます。
待機中のマージを次のマージで置き換えません。取り消された実行は同じ適格マージで再実行します。
固定版actionlintが未対応の`queue`は別途検証し、他のlint検証は維持します。

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

Release caches are restore-only: compiler `cache-from` and library
`actions/cache/restore` keep their keys, scopes and paths. There is no GHA export,
cache save (including post/failure paths), or cache-write privilege override.
Buildx setup uses `cache-binary: false` to prevent its implicit remote binary
cache post-save, including on runners without preinstalled Buildx.
Local reuse and package/source checks remain in place. Absent or evicted caches
may require a cold build when a release is later run; feature-branch native-trial
caches cannot be assumed to populate default-branch release caches.
The staged workflow permits compiler export and unsigned-stage saves only for
intentional `workflow_dispatch` or `pull_request` validation, with event/ref-scoped
cache visibility. PR merge-ref caches are not generally visible to a later main
release. The dispatch ref determines cache scope; `source-ref` only selects source.
Restores are attempts, and optional compiler exports with `ignore-error=true`
are not evidence of verified hits or successful warming. Hosted warm acceptance
and capacity remain NOT_RUN.

Existing compiler artifacts remain bound to the original source commit and run.
Same-run recovery revalidates those bindings; it cannot establish acceptance of
a new commit. Rerunning an old run uses its old workflow/code and does not apply
this fix. Do not relabel or substitute artifacts across runs.

The 2026-11-11 rule-inspection snapshot records
[main ruleset 26762240](https://github.com/michibiki-io/shirube-os/rules/26762240):
PR approval (including Code Owner review and resolved review threads), deletion
protection and non-fast-forward protection. No required-status-check or merge-queue
rule was returned; classic branch protection reported `Branch not protected`.
This snapshot is not a guarantee of future rules, and no repository settings
were changed. If status-check or merge-queue requirements change,
reassess the PR-only event mapping; Test currently has no `merge_group` coverage.

`Test`はPRの`opened`、`synchronize`、`reopened`、`ready_for_review`だけで起動します。
PRへの追加commitは`synchronize`で検証します。mainや他ブランチへの直接push、タグ、
PRのclose、手動実行、`merge_group`では起動しません。ジョブ名とrunnerは維持します。
リリースの`resolve`は、ビルド前に正確なマージ済みソースを引き続き検証します。
Testの成功だけではイメージの合格を意味しません。native trialはfeature branchでの
明示的な手動実行と既存のキャッシュ動作を維持します。

リリースではコンパイラーとライブラリーのキャッシュを復元するだけで、キー、スコープ、
パスを維持します。post処理や失敗時を含め、GHAへのexportや保存、書き込み権限の拡張は
行いません。Buildx未導入のrunnerでも暗黙のpost保存が起きないよう、setupには
`cache-binary: false`を指定します。ローカル再利用とパッケージ、ソースの検証は維持します。
キャッシュの欠落や削除により、後日のリリース実行でコールドビルドが必要になる場合が
あります。feature branchのnative trialによる既定ブランチのキャッシュ補充は保証しません。
ステージ分割workflowでコンパイラーのexportと署名前ステージの保存を許可するのは、
明示的な検証イベントの`workflow_dispatch`と`pull_request`だけです。
キャッシュの可視範囲はイベントとrefに依存し、PRのmerge refのキャッシュを
後日のmainリリースで利用できるとは限りません。
手動実行ではdispatch refがキャッシュのスコープを決め、`source-ref`はソースだけを選びます。
復元の試行や`ignore-error=true`による任意のexportは、検証済みのヒットや保存成功の証拠にはなりません。
ホスト上のwarm合格判定と容量の検証はNOT_RUNです。

既存のコンパイラー成果物は元のソースcommitとrunに結び付いたままです。
同じrunの復旧でもこの対応を再検証し、新しいcommitの合格根拠にはできません。
古いrunの再実行は古いworkflowとコードを使うため、この修正は適用されません。
別runの成果物への付け替えや、ラベルを変更した流用はしません。

2026-11-11の確認記録では、[mainのruleset 26762240](https://github.com/michibiki-io/shirube-os/rules/26762240)に
PR承認、Code Ownerレビュー、レビュースレッドの解決、削除禁止、non-fast-forward禁止が
あります。required status checkとmerge queueのルールは返されず、従来のbranch protectionは
`Branch not protected`でした。この記録は将来のルールを保証せず、設定も変更していません。
status checkやmerge queueの要件が変わる場合は、PR専用の起動条件を再確認します。
現状のTestは`merge_group`を検証しません。

`producer.policy.eligible` accepts relevant PRs merged into main and their exact
40-character merge commit. Unmerged PRs, docs-only changes and direct pushes are
excluded from expensive builds and publication. Relevant paths are producer,
containers, source lock, patches, guest integration, tests, workflow/schema and
logo changes. The project policy intentionally uses native AMD64/ARM64 runner variables and
requires a new Shirube revision for every relevant eligible merged commit. Those issue-specific
rules override generic container-registry/QEMU release defaults.

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
job's old outputs without running that job again.
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
`producer.actions.resolve` still excludes only the five exact `PUBLICATION_PATHS`
entries before identity checks, release lookup or version allocation. The three
retired entries (`.github/workflows/recover-release.yml`,
`producer/recover_release.py`, `tests/test_recover_release.py`) remain because
removed files are included in the deletion PR's complete changed-file inventory.
Together with `producer/actions.py` and `tests/test_actions.py`, these exact
exemptions keep that PR non-image after merge. Both rename origins and destinations
are classified; unknown relevant paths and mixed image changes remain eligible.
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
`producer.actions.resolve`はidentity確認、release取得、版の割り当てより前に、
`PUBLICATION_PATHS`の5つの完全一致パスだけを除外します。
削除した3パス（`.github/workflows/recover-release.yml`、`producer/recover_release.py`、
`tests/test_recover_release.py`）も削除PRの全変更一覧に含まれるため、除外に残します。
`producer/actions.py`、`tests/test_actions.py`と合わせて、削除PRをmerge後もイメージ対象外に保ちます。
rename元とrename先の両方を判定し、未知の関連パスやイメージ入力との混合変更は対象のままです。
今回の完了によって、無関係な過去のhosted、OCI、cacheの検証記録は変わりません。
