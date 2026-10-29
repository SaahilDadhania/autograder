#include <stdio.h>
#include <stdlib.h>

/* Stores the sum on the heap but never frees it -> LeakSanitizer reports a leak. */
long long add_on_heap(long long a, long long b) {
    long long *result = malloc(sizeof *result);   /* BUG: never freed */
    if (!result) return 0;
    *result = a + b;
    return *result;
}

int main(void) {
    long long a, b;
    if (scanf("%lld %lld", &a, &b) != 2) return 1;
    printf("%lld\n", add_on_heap(a, b));
    return 0;
}
