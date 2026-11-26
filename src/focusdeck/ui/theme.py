"""Visual styling for the native widget and its dialogs."""

WIDGET_CSS = """
body {
    --fd-surface: #ffffff;
    --fd-ink: #202842;
    --fd-muted: #535e76;
    --fd-border: #dce1ee;
    --fd-field-border: #7e8aa1;
    background: #eff2f8;
    color: var(--fd-ink);
    font-family: 'Segoe UI', system-ui, sans-serif;
}
body.body--dark {
    --q-primary: #b9aaff !important;
    --fd-surface: #181f31;
    --fd-ink: #f0f2ff;
    --fd-muted: #aeb9d3;
    --fd-border: #333d55;
    --fd-field-border: #74819d;
    background: #101625;
}
.nicegui-content { padding: 10px; }
.fd-widget { max-width: 480px; margin-inline: auto; }
.fd-timer {
    --fd-accent: #6551d1;
    --fd-phase-bg: #eeebff;
    background: linear-gradient(155deg, var(--fd-phase-bg), var(--fd-surface) 69%);
    border: 1px solid var(--fd-border);
    border-radius: 16px;
    padding: 12px;
}
.fd-timer.fd-short-break { --fd-accent: #00765e; --fd-phase-bg: #dff6ee; }
.fd-timer.fd-long-break { --fd-accent: #245ea9; --fd-phase-bg: #e2efff; }
.body--dark .fd-timer { --fd-accent: #c4b8ff; --fd-phase-bg: #30264c; }
.body--dark .fd-timer.fd-short-break { --fd-accent: #83e2c9; --fd-phase-bg: #193a38; }
.body--dark .fd-timer.fd-long-break { --fd-accent: #a4ccff; --fd-phase-bg: #203653; }
.fd-phase {
    color: var(--fd-accent);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .115em;
    text-transform: uppercase;
}
.fd-time {
    font-size: clamp(48px, 18vw, 68px);
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    letter-spacing: -.055em;
    line-height: 1;
    color: var(--fd-ink);
}
.fd-muted { color: var(--fd-muted); }
.fd-card {
    background: var(--fd-surface);
    border: 1px solid var(--fd-border);
    border-radius: 12px;
    padding: 8px;
}
.fd-widget .q-btn { border-radius: 8px; text-transform: none; }
.fd-widget .q-btn__content { text-transform: none; }
.body--dark .q-btn.bg-primary { --q-primary: #5945c7; }
.fd-widget .q-field__control { border-radius: 8px; }
.fd-widget .q-field--outlined .q-field__control::before { border-color: var(--fd-field-border); }
.fd-widget .q-btn:focus-visible { outline: 2px solid var(--fd-muted); outline-offset: 2px; }
.q-dialog .q-card {
    background: var(--fd-surface);
    color: var(--fd-ink);
    border: 1px solid var(--fd-border);
    border-radius: 14px;
}
"""
