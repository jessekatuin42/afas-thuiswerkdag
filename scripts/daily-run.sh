#!/usr/bin/env bash
#
# Unattended daily Thuiswerkdag declaration, driven by a systemd timer.
#
# The work-from-home signal is *presence*: this machine is only powered on at
# 11:00 on days worked from home. On office days it is off, the timer never
# fires, and no declaration is made.
#
# That makes catch-up firing the central hazard. systemd replays a missed
# OnCalendar run — after a boot (Persistent=true) or on resume from suspend —
# and such a replay means precisely "this machine was NOT on at 11:00", i.e. an
# office day. Every replay outside the grace window is therefore refused.
#
# Two properties keep that safe rather than merely strict:
#   * The window still permits a genuinely late start (booting at 11:40 from
#     home is honoured; resuming at 16:00 is not).
#   * The declaration is always for *today* (--today), never back-dated, so a
#     replay can never file a declaration for the day that was missed.

set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
ENTRY="$REPO/afas_thuiswerk.py"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/afas-thuiswerk"
LOG="$STATE_DIR/run.log"
LOCK="$STATE_DIR/run.lock"
PAUSE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/afas-thuiswerk/pause"

# Grace window as minutes since midnight. A fire outside this is a replay.
WINDOW_START=$((11 * 60))       # 11:00
WINDOW_END=$((11 * 60 + 59))    # 11:59

mkdir -p "$STATE_DIR"

# --- Guard 0: reason about *this machine's* clock ----------------------------
# Every guard below asks "was I at my desk at 11:00 Amsterdam time?". `date`
# answers in whatever zone TZ names, so an invoker carrying TZ=UTC shifts the
# whole window two hours: a 13:00 run reads its own clock as 11:00, walks
# through the presence window, and files an afternoon run as the morning one.
# That happened. Pin the zone to the system's own, so the guards mean what they
# say no matter who starts us. AFAS_TZ overrides, for testing the window logic.
system_tz=$(readlink -f /etc/localtime 2>/dev/null | sed -n 's#.*/zoneinfo/##p')
TZ="${AFAS_TZ:-$system_tz}"
# An empty TZ is not "unset" to glibc — it means UTC. Prefer the loader default.
if [[ -n "$TZ" ]]; then export TZ; else unset TZ; fi

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

alert() {
    log "ALERT $*"
    notify-send -u critical "AFAS Thuiswerkdag" "$*" 2>/dev/null || true
}

trim_log() {
    [[ -f "$LOG" ]] || return 0
    local lines
    lines=$(wc -l <"$LOG")
    if ((lines > 2000)); then
        tail -n 1000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
}

# --- Guard 1: weekdays only -------------------------------------------------
# Belt and braces alongside the timer's Mon..Fri: a Friday run replayed after a
# weekend boot would otherwise arrive on a Saturday.
dow=$(date +%u)
if ((dow > 5)); then
    log "SKIP  weekend (ISO day $dow)"
    trim_log
    exit 0
fi

# --- Guard 2: grace window --------------------------------------------------
now=$((10#$(date +%H) * 60 + 10#$(date +%M)))
if ((now < WINDOW_START || now > WINDOW_END)); then
    log "SKIP  fired $(date +%H:%M), outside the $(printf '%02d:%02d' $((WINDOW_START / 60)) $((WINDOW_START % 60)))-$(printf '%02d:%02d' $((WINDOW_END / 60)) $((WINDOW_END % 60))) window — replay of a day this machine was off"
    trim_log
    exit 0
fi

# --- Guard 3: manual pause (holidays, leave) --------------------------------
if [[ -f "$PAUSE_FILE" ]]; then
    until_date=$(tr -d '[:space:]' <"$PAUSE_FILE")
    today=$(date +%F)
    if [[ -z "$until_date" ]]; then
        log "SKIP  paused indefinitely ($PAUSE_FILE)"
        trim_log
        exit 0
    elif [[ ! "$until_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        # Unreadable pause file: skip rather than guess. Declining to declare is
        # recoverable by hand; a wrong declaration is not.
        log "SKIP  unreadable pause date '$until_date' in $PAUSE_FILE — refusing to guess"
        trim_log
        exit 0
    elif [[ "$today" < "$until_date" || "$today" == "$until_date" ]]; then
        log "SKIP  paused until $until_date (inclusive)"
        trim_log
        exit 0
    else
        log "pause expired on $until_date — removing $PAUSE_FILE"
        rm -f "$PAUSE_FILE"
    fi
fi

# --- Guard 4: one run at a time ---------------------------------------------
exec 9>"$LOCK"
if ! flock -n 9; then
    log "SKIP  another run holds the lock"
    trim_log
    exit 0
fi

# --- Run --------------------------------------------------------------------
log "RUN   declaring Thuiswerkdag for $(date +%F)"

output=$("$PYTHON" "$ENTRY" --today --headless --quiet 2>&1)
rc=$?

printf '%s\n' "$output" | sed '/^[[:space:]]*$/d; s/^/      /' >>"$LOG"

case $rc in
    0)
        if grep -q 'Already exists' <<<"$output"; then
            log "OK    already declared, nothing created"
        else
            log "OK    created"
        fi
        ;;
    2)
        # The one state where AFAS may or may not hold a record. Never retried.
        alert "UNCONFIRMED for $(date +%F) — a submission may or may not have registered. Check AFAS by hand."
        ;;
    *)
        alert "FAILED (exit $rc) for $(date +%F) — no declaration was created."
        ;;
esac

trim_log
exit $rc
