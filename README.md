# dev-helper
FocusDeck is a Windows desktop widget for Pomodoro sessions, music, and study tracking.

## Study tracking

From the timer, add your skills with **+**, then open **Study**. Use **Targets** to
save whole percentages totaling 100%. In **Roadmaps**, import a roadmap and use
**Skill to link** to assign its topics or project paths to a skill. Back in the
timer, select the skill and an optional topic before starting focus. Positive
logged time marks a topic Learning. Saving a recap on a completed focus block
marks it Claimed. A perfect recall quiz or a confirmed passing artifact review marks
the topic Verified.

Run FocusDeck from the repository root to import from `data/roadmaps/`. For a
local roadmap.sh checkout, enter the relative path to its roadmap folder, such
as `developer-roadmap/roadmaps/backend`. FocusDeck reads topic IDs from the
Markdown filenames. The current upstream checkout supplies a list of topics
and the source link, with no graph order. You can also put a UTF-8 YAML file
in that directory and enter its relative filename:

```yaml
id: web-basics
name: Web Basics
nodes:
  - id: http
    title: HTTP request and response basics
    url: https://developer.mozilla.org/en-US/docs/Web/HTTP
```

Keep the IDs stable when editing and re-importing a roadmap so FocusDeck can
preserve your links and progress.

In **Study → Time**, each finished or skipped focus block contributes its
recorded active time to the local date it ended. Targets are weighted by the
active time logged under each version. The view leaves the gap blank below
120 minutes and when logged time predates the first saved target.

## Claude Code capture

Open **Capture** in the timer. Review the excluded and metadata-only project lists,
check the review box, and save the policy. Use **Install hooks** to preview the
Claude settings change and **Apply settings change** to confirm. Enable capture
and save the policy when ready. Use **Uninstall hooks** to preview removing
FocusDeck's hooks. Settings changes keep a uniquely named backup beside the
settings file.

On Windows, FocusDeck's hooks launch directly with the current Python executable.
The hook writes local metadata to a spool. While the widget is open it imports
new events and redacted details into SQLite. It uses project path fingerprints
for policy checks and follows only transcript paths beneath the configured
Claude projects directory. Exclusions cover subfolders and win over metadata-only
rules. Policy changes govern future capture and ingestion. Previously imported
records remain in SQLite.

For a scratch setup, run from the repository root with
`FOCUSDECK_DATA_DIR` and `FOCUSDECK_CLAUDE_CONFIG_DIR` set to separate local scratch
folders. The second variable selects the directory containing scratch
`settings.json` and `projects/`. Without an override, FocusDeck uses
`CLAUDE_CONFIG_DIR`, or the usual home-directory `.claude` folder.

## Day view and reports

Open **Day** and choose a local calendar date. Expand a quarter or **Off-hours**
for its work-type and skill totals, completed Pomodoros, recaps and roadmap
evidence. FocusDeck counts overlapping activity once and gives tagged focus
blocks precedence. Older paused blocks use an estimated placement because
their pause timestamps were unavailable.

Use **Setup** to edit quarter hours or correct a captured Claude session's work
type and optional skill for that date. Use **Charts** to inspect the timeline,
work-type breakdown, skill allocation, or current roadmap progress.

**Save Markdown + HTML** writes both reports to a dated folder under
`reports/` in FocusDeck's data directory. Keep the PNGs next to the Markdown
report. The HTML report embeds its images and opens offline.

## AI reviewers

In **Day → Review**, choose Work Critic, Study Judge, Summarize, Classify, or
Recall quiz. **Preview request** displays the anonymized digest. An approved
request uses a tool-free Claude Code subprocess and displays usage when available.
Quarter-end reminders queue a preview and still require approval to send.

Review digests use local counts, anonymous references and public quiz subjects.
Prompt excerpts, recaps, filenames, project identifiers, Git messages and
project instructions stay local. The optional checkout field supplies counts and
rule indicators only. Recall quizzes are limited to supported public subjects.
FocusDeck grades answers locally and records E2 evidence for a 3/3 result.
Applied classification suggestions preserve any manual correction.

### Artifact verification

In **Study → Roadmaps**, select an active topic and choose **Review artifact**.
Enter a local artifact reference, a shareable criterion describing what the topic
requires, and a short sample cleared for sharing. FocusDeck sends only the criterion
and sample as input after you preview and approve the request. It keeps the artifact
reference local and does not read or upload the file.

Study Judge shows its verdict and any quoted evidence. If the sample demonstrates
the topic, choose **Confirm and record E2** to save the verdict and mark the topic
Verified. The evidence appears in the Day report. Confirming the same criterion
and sample again for the same topic does not add another evidence row.
