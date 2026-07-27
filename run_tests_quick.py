import sys
from tests.test_research_overhaul import *

def run():
    test_completeness_gaps_full()
    test_completeness_gaps_blank_evidence()
    test_completeness_gaps_role_true_no_source()
    test_completeness_gaps_no_contact_or_read()
    test_post_process_sorts_staleness()
    test_post_process_display_name()
    test_research_capped()
    print("All tests passed!")

if __name__ == "__main__":
    run()
