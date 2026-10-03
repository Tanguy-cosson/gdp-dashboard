from demo_history import load_demo_history
from db import get_connection

if __name__ == "__main__":
    result = load_demo_history(get_connection())
    print(result["message"])
