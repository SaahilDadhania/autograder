#include <stdio.h>

static long long sum_two(long long x, long long y) {
    return x + y;
}

int main(void) {
    long long first, second;
    if (scanf("%lld %lld", &first, &second) != 2) {
        return 1;
    }
    printf("%lld\n", sum_two(first, second));
    return 0;
}
