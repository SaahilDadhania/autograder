#include <stdio.h>
#include <stdlib.h>
/* BUG: uses abs(b), so negative second numbers give wrong sums */
long long add(long long a, long long b) { return a + abs((int)b); }
int main(void) {
    long long a, b;
    scanf("%lld %lld", &a, &b);
    printf("%lld\n", add(a, b));
    return 0;
}
