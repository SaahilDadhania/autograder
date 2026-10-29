#include <stdio.h>

/* Adds two 64-bit integers; long long avoids overflow for any 32-bit input. */
static long long add(long long lhs, long long rhs) {
    return lhs + rhs;
}

/* Reads two integers from stdin and prints their sum. */
int main(void) {
    long long lhs, rhs;
    if (scanf("%lld %lld", &lhs, &rhs) != 2) {
        return 1; /* malformed input */
    }
    printf("%lld\n", add(lhs, rhs));
    return 0;
}
