/* Instructor's reference solution: uses 64-bit integers so the sum never overflows.
   The agent compiles this once and uses its output as the "expected" answer for
   edge cases and for LLM-proposed counter-tests (differential testing). */
#include <stdio.h>

int main(void) {
    long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) {
        return 1;               /* invalid input -> the agent discards that test */
    }
    printf("%lld\n", a + b);
    return 0;
}
