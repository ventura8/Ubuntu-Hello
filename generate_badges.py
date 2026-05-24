#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import py_compile

def run_tests_and_get_coverage():
    print("Running pytest to gather coverage data...")
    # Run pytest with coverage to generate the latest coverage data
    subprocess.run(
        ["pytest", "--cov=ubuntu-hello-gtk", "--cov=ubuntu-hello", "tests/"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Write JSON report
    subprocess.run(
        ["coverage", "json", "-o", "coverage.json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    if not os.path.exists("coverage.json"):
        print("Error: coverage.json was not generated.")
        return None
        
    with open("coverage.json", "r") as f:
        data = json.load(f)
        
    # Clean up coverage.json
    try:
        os.remove("coverage.json")
    except Exception:
        pass
        
    return data.get("totals", {}).get("percent_covered")

def check_lint_status():
    print("Linting Python files...")
    # Compile all python files in the workspace to verify syntax/lint correctness
    has_errors = False
    for root, dirs, files in os.walk("."):
        # Skip hidden directories and build directories
        if any(p in root for p in [".git", ".pytest_cache", ".cache", "build", "builddir"]):
            continue
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                try:
                    py_compile.compile(path, doraise=True)
                except Exception as e:
                    print(f"Lint/Syntax error in {path}: {e}")
                    has_errors = True
    return "failing" if has_errors else "passing"

def generate_coverage_badge(percentage, output_path):
    # Determine color based on percentage
    if percentage >= 90:
        color = "#4c1"  # bright green
    elif percentage >= 80:
        color = "#97ca00"  # green
    elif percentage >= 70:
        color = "#dfb317"  # yellow
    elif percentage >= 60:
        color = "#fe7d37"  # orange
    else:
        color = "#e05d44"  # red

    percent_str = f"{round(percentage)}%"
    
    # SVG template for coverage badge (width 100)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="20" role="img" aria-label="coverage: {percent_str}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="100" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="61" height="20" fill="#555"/>
    <rect x="61" width="39" height="20" fill="{color}"/>
    <rect width="100" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
    <text aria-hidden="true" x="315" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="510">coverage</text>
    <text x="315" y="140" transform="scale(.1)" fill="#fff" textLength="510">coverage</text>
    <text aria-hidden="true" x="805" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="290">{percent_str}</text>
    <text x="805" y="140" transform="scale(.1)" fill="#fff" textLength="290">{percent_str}</text>
  </g>
</svg>"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(svg)
    print(f"Generated coverage badge at {output_path} ({percent_str})")

def generate_lint_badge(status, output_path):
    color = "#4c1" if status == "passing" else "#e05d44"
    
    # SVG template for linting badge (width 104)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="104" height="20" role="img" aria-label="linting: {status}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="104" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="47" height="20" fill="#555"/>
    <rect x="47" width="57" height="20" fill="{color}"/>
    <rect width="104" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
    <text aria-hidden="true" x="245" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="370">linting</text>
    <text x="245" y="140" transform="scale(.1)" fill="#fff" textLength="370">linting</text>
    <text aria-hidden="true" x="745" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="470">{status}</text>
    <text x="745" y="140" transform="scale(.1)" fill="#fff" textLength="470">{status}</text>
  </g>
</svg>"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(svg)
    print(f"Generated linting badge at {output_path} ({status})")

def main():
    coverage_pct = run_tests_and_get_coverage()
    if coverage_pct is None:
        print("Could not retrieve coverage percentage.")
        coverage_pct = 0.0
        
    lint_status = check_lint_status()
    
    generate_coverage_badge(coverage_pct, "docs/badges/coverage.svg")
    generate_lint_badge(lint_status, "docs/badges/linting.svg")

if __name__ == "__main__":
    main()
