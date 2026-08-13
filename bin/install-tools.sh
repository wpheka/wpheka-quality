#!/usr/bin/env bash
#
# Install the check tools WPHEKA Quality can drive, system-wide.
#
# Idempotent: anything already present is left alone. Nothing here touches a
# repository under review — this only installs tooling for the current user.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_BIN="${HOME}/.local/bin"
DRY_RUN=0
LINK_CLI=1

usage() {
  cat <<EOF
Install the WPHEKA Quality toolchain.

Usage: install-tools.sh [--dry-run] [--no-link]

  --dry-run   Print what would be installed without installing it
  --no-link   Do not symlink wpheka-quality into ~/.local/bin

Installs: phpcs + WordPress standards, phpstan + WordPress/WooCommerce stubs,
phpunit, semgrep, gitleaks. Homebrew is used where a formula exists; composer
and PHARs otherwise. Homebrew, php, composer and git are prerequisites.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-link) LINK_CLI=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

info() { printf '\033[36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$1" >&2; }
skip() { printf '    %s is already installed\n' "$1"; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '    would run: %s\n' "$*"
  else
    printf '    %s\n' "$*"
    "$@"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# --- prerequisites ---------------------------------------------------------
info "Checking prerequisites"
MISSING_PREREQ=0
for tool in git php composer; do
  if have "$tool"; then
    skip "$tool"
  else
    warn "$tool is required but not installed"
    MISSING_PREREQ=1
  fi
done
if [[ $MISSING_PREREQ -eq 1 ]]; then
  echo "Install the prerequisites above first." >&2
  exit 1
fi

mkdir -p "$LOCAL_BIN"

# --- Scanners --------------------------------------------------------------
# Homebrew has both; elsewhere semgrep comes from pip and gitleaks from its
# release tarball, since distro packages for these lag badly.
info "Installing scanners"

install_semgrep() {
  if have brew; then
    run brew install semgrep
  elif have pipx; then
    run pipx install semgrep
  elif have pip3; then
    run pip3 install --user semgrep
  else
    warn "no brew, pipx or pip3 found — install semgrep manually; the check will be SKIPPED"
  fi
}

install_gitleaks() {
  if have brew; then
    run brew install gitleaks
    return
  fi
  local os arch url
  case "$(uname -s)" in
    Linux) os="linux" ;;
    Darwin) os="darwin" ;;
    *) warn "unsupported OS for automatic gitleaks install; the check will be SKIPPED"; return ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) warn "unsupported architecture for automatic gitleaks install"; return ;;
  esac
  url="https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_${os}_${arch}.tar.gz"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '    would download %s into %s\n' "$url" "$LOCAL_BIN"
    return
  fi
  printf '    downloading gitleaks\n'
  if curl -fsSL "$url" | tar -xz -C "$LOCAL_BIN" gitleaks 2>/dev/null; then
    chmod +x "$LOCAL_BIN/gitleaks"
  else
    warn "could not download gitleaks; the check will be SKIPPED"
  fi
}

if have semgrep; then skip "semgrep"; else install_semgrep; fi
if have gitleaks; then skip "gitleaks"; else install_gitleaks; fi

# --- Composer global packages ----------------------------------------------
info "Installing PHP analysis tools via Composer"

# phpcs alone is not enough: without the WordPress standard registered, it
# reviews against PSR defaults and quietly reports the wrong things.
if have phpcs && phpcs -i 2>/dev/null | grep -q "WordPress"; then
  skip "phpcs with the WordPress standard"
else
  # Composer 2.2+ blocks plugins unless allowed. The codesniffer installer is
  # what registers the WordPress standard with phpcs, so without this the
  # require aborts and phpcs ends up with no WordPress ruleset.
  run composer global config --no-plugins \
    allow-plugins.dealerdirect/phpcodesniffer-composer-installer true
  run composer global require --no-interaction \
    squizlabs/php_codesniffer \
    wp-coding-standards/wpcs \
    phpcsstandards/phpcsextra \
    dealerdirect/phpcodesniffer-composer-installer
fi

if have phpstan; then
  skip "phpstan"
else
  # The stubs let PHPStan resolve WordPress and WooCommerce symbols instead of
  # reporting every core function as undefined.
  run composer global require --no-interaction \
    phpstan/phpstan \
    szepeviktor/phpstan-wordpress \
    php-stubs/wordpress-stubs \
    php-stubs/woocommerce-stubs
fi

# --- PHPUnit ---------------------------------------------------------------
info "Installing PHPUnit"
if have phpunit; then
  skip "phpunit"
elif [[ $DRY_RUN -eq 1 ]]; then
  printf '    would download phpunit.phar into %s\n' "$LOCAL_BIN"
else
  # A PHAR keeps PHPUnit's dependency tree out of the shared global composer
  # install, where it would fight with phpcs and phpstan over versions.
  printf '    downloading phpunit.phar\n'
  if curl -fsSL -o "$LOCAL_BIN/phpunit.tmp" https://phar.phpunit.de/phpunit.phar; then
    chmod +x "$LOCAL_BIN/phpunit.tmp"
    mv "$LOCAL_BIN/phpunit.tmp" "$LOCAL_BIN/phpunit"
  else
    rm -f "$LOCAL_BIN/phpunit.tmp"
    warn "could not download phpunit.phar; the phpunit check will be SKIPPED"
  fi
fi

# --- WordPress Plugin Check ------------------------------------------------
info "WordPress Plugin Check"
if have wp; then
  if [[ -n "${WPHEKA_WP_PATH:-}" ]]; then
    if wp --path="$WPHEKA_WP_PATH" cli has-command "plugin check" >/dev/null 2>&1; then
      skip "plugin-check in $WPHEKA_WP_PATH"
    else
      run wp --path="$WPHEKA_WP_PATH" plugin install plugin-check --activate
    fi
  else
    printf '    plugin-check is a WordPress plugin, not a standalone binary.\n'
    printf '    Install it into your WordPress site:\n'
    printf '      wp --path=/path/to/wordpress plugin install plugin-check --activate\n'
    printf '    The engine finds the site by walking up from the plugin directory,\n'
    printf '    or from $WPHEKA_WP_PATH.\n'
  fi
else
  warn "wp-cli not installed; the plugin_check check will be SKIPPED (brew install wp-cli)"
fi

# --- CLI on PATH -----------------------------------------------------------
if [[ $LINK_CLI -eq 1 ]]; then
  info "Linking wpheka-quality into $LOCAL_BIN"
  if [[ -L "$LOCAL_BIN/wpheka-quality" && "$(readlink "$LOCAL_BIN/wpheka-quality")" == "$ROOT/bin/wpheka-quality" ]]; then
    skip "wpheka-quality symlink"
  else
    run ln -sf "$ROOT/bin/wpheka-quality" "$LOCAL_BIN/wpheka-quality"
  fi
fi

case ":$PATH:" in
  *":$LOCAL_BIN:"*) ;;
  *) warn "$LOCAL_BIN is not on your PATH. Add to ~/.zshrc:"
     echo '       export PATH="$HOME/.local/bin:$PATH"' ;;
esac
COMPOSER_BIN="$(composer global config bin-dir --absolute 2>/dev/null || echo "$HOME/.composer/vendor/bin")"
case ":$PATH:" in
  *":$COMPOSER_BIN:"*) ;;
  *) warn "$COMPOSER_BIN is not on your PATH. Add to ~/.zshrc:"
     echo "       export PATH=\"$COMPOSER_BIN:\$PATH\"" ;;
esac

echo ""
info "Done. Verify with:"
echo "    wpheka-quality --doctor"
