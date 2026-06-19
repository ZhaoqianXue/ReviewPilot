"""
Human-in-the-loop interaction utilities.
Provides CLI prompts for user input during pipeline execution.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime


def print_header(title: str, width: int = 70):
    """
    Print a formatted header for a pipeline stage.

    Args:
        title: Header title
        width: Total width of the header box
    """
    border = "=" * width
    print(f"\n{border}")
    print(f"  {title}")
    print(f"{border}\n")


def print_subheader(title: str):
    """Print a subheader with dashes."""
    print(f"\n{title}")
    print("-" * len(title))


def print_summary(data: Dict[str, Any], title: str = "Summary"):
    """
    Print a formatted summary of key-value pairs.

    Args:
        data: Dictionary of items to display
        title: Summary title
    """
    print(f"\n{title}:")
    for key, value in data.items():
        print(f"  {key}: {value}")
    print()


def print_box(content: str, width: int = 70, wrap: bool = True):
    """
    Print content in a box with word wrapping.

    Args:
        content: Text content to display
        width: Box width
        wrap: If True, wrap long lines; if False, truncate
    """
    import textwrap

    inner_width = width - 4  # Account for "| " and " |"
    lines = content.split('\n')
    border = "+" + "-" * (width - 2) + "+"

    print(border)
    for line in lines:
        if len(line) <= inner_width:
            print(f"| {line:<{inner_width}} |")
        elif wrap:
            # Wrap long lines
            wrapped = textwrap.wrap(line, width=inner_width)
            for wrapped_line in wrapped:
                print(f"| {wrapped_line:<{inner_width}} |")
        else:
            # Truncate
            print(f"| {line[:inner_width - 3]}... |")
    print(border)


def print_text(content: str, indent: int = 2):
    """
    Print text content with indentation (no box).

    Args:
        content: Text content to display
        indent: Number of spaces to indent
    """
    prefix = " " * indent
    for line in content.split('\n'):
        print(f"{prefix}{line}")


def ask_text(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    """
    Ask user for text input.

    Args:
        prompt: The question to ask
        default: Default value if user presses Enter
        required: If True, keep asking until non-empty input

    Returns:
        User's input string
    """
    default_text = f" [{default}]" if default else ""

    while True:
        try:
            response = input(f"? {prompt}{default_text}: ").strip()

            if not response and default is not None:
                return default

            if not response and required:
                print("  This field is required. Please enter a value.")
                continue

            return response

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def ask_confirm(prompt: str, default: bool = True) -> bool:
    """
    Ask user for yes/no confirmation.

    Args:
        prompt: The question to ask
        default: Default value if user presses Enter

    Returns:
        True for yes, False for no
    """
    default_text = "Y/n" if default else "y/N"

    while True:
        try:
            response = input(f"? {prompt} ({default_text}): ").strip().lower()

            if not response:
                return default

            if response in ('y', 'yes'):
                return True
            elif response in ('n', 'no'):
                return False
            else:
                print("  Please enter 'y' or 'n'.")

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def ask_choice(prompt: str, options: List[str], default: Optional[int] = None) -> int:
    """
    Ask user to choose from a list of options.

    Args:
        prompt: The question to ask
        options: List of option strings
        default: Default option index (0-based)

    Returns:
        Index of chosen option (0-based)
    """
    print(f"\n? {prompt}")

    for i, option in enumerate(options):
        marker = "*" if i == default else " "
        print(f"  {marker} {i + 1}. {option}")

    default_text = f" [{default + 1}]" if default is not None else ""

    while True:
        try:
            response = input(f"  Enter choice (1-{len(options)}){default_text}: ").strip()

            if not response and default is not None:
                return default

            try:
                choice = int(response)
                if 1 <= choice <= len(options):
                    return choice - 1
                else:
                    print(f"  Please enter a number between 1 and {len(options)}.")
            except ValueError:
                print("  Please enter a valid number.")

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def ask_multiselect(prompt: str, options: List[str], defaults: Optional[List[int]] = None) -> List[int]:
    """
    Ask user to select multiple options.

    Args:
        prompt: The question to ask
        options: List of option strings
        defaults: List of default selected indices

    Returns:
        List of selected indices (0-based)
    """
    if defaults is None:
        defaults = []

    print(f"\n? {prompt}")
    print("  (Enter numbers separated by commas, or 'all' for all options)")

    for i, option in enumerate(options):
        marker = "[x]" if i in defaults else "[ ]"
        print(f"  {marker} {i + 1}. {option}")

    default_text = ""
    if defaults:
        default_text = f" [{','.join(str(d + 1) for d in defaults)}]"

    while True:
        try:
            response = input(f"  Enter choices{default_text}: ").strip().lower()

            if not response and defaults:
                return defaults

            if response == 'all':
                return list(range(len(options)))

            try:
                choices = []
                for part in response.split(','):
                    part = part.strip()
                    if part:
                        choice = int(part)
                        if 1 <= choice <= len(options):
                            choices.append(choice - 1)
                        else:
                            print(f"  Invalid choice: {choice}. Must be 1-{len(options)}.")
                            choices = None
                            break

                if choices is not None:
                    return sorted(set(choices))  # Remove duplicates and sort

            except ValueError:
                print("  Please enter valid numbers separated by commas.")

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def ask_date(prompt: str, default: Optional[str] = None) -> Optional[str]:
    """
    Ask user for a date in YYYY-MM-DD format.

    Args:
        prompt: The question to ask
        default: Default date string

    Returns:
        Date string in YYYY-MM-DD format, or None if 'any'
    """
    default_text = f" [{default}]" if default else ""

    while True:
        try:
            response = input(f"? {prompt}{default_text}: ").strip().lower()

            if not response and default:
                return default

            if response in ('any', 'none', ''):
                return None

            if response == 'today':
                return datetime.now().strftime('%Y-%m-%d')

            # Validate date format
            try:
                datetime.strptime(response, '%Y-%m-%d')
                return response
            except ValueError:
                print("  Please enter a valid date in YYYY-MM-DD format, 'today', or 'any'.")

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def ask_number(prompt: str, default: Optional[int] = None, min_val: int = 0, max_val: Optional[int] = None) -> int:
    """
    Ask user for a number.

    Args:
        prompt: The question to ask
        default: Default value
        min_val: Minimum allowed value
        max_val: Maximum allowed value (None for unlimited)

    Returns:
        User's number input
    """
    default_text = f" [{default}]" if default is not None else ""
    range_text = f" ({min_val}-{max_val if max_val else 'unlimited'})"

    while True:
        try:
            response = input(f"? {prompt}{range_text}{default_text}: ").strip()

            if not response and default is not None:
                return default

            try:
                value = int(response)
                if value < min_val:
                    print(f"  Value must be at least {min_val}.")
                    continue
                if max_val is not None and value > max_val:
                    print(f"  Value must be at most {max_val}.")
                    continue
                return value
            except ValueError:
                print("  Please enter a valid number.")

        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            raise SystemExit(1)


def show_progress(current: int, total: int, prefix: str = "Progress", width: int = 40):
    """
    Show a simple progress bar.

    Args:
        current: Current progress value
        total: Total value
        prefix: Text prefix
        width: Width of the progress bar
    """
    if total == 0:
        percent = 100
    else:
        percent = int((current / total) * 100)

    filled = int(width * current / total) if total > 0 else width
    bar = "=" * filled + "-" * (width - filled)

    print(f"\r{prefix}: [{bar}] {percent}% ({current}/{total})", end='', flush=True)

    if current == total:
        print()  # New line when complete


def pause(message: str = "Press Enter to continue..."):
    """Pause and wait for user to press Enter."""
    try:
        input(f"\n{message}")
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        raise SystemExit(1)
