#include <stdio.h>
int main(void) {
    int a, b;
    scanf("%d %d", &a, &b);
    while (a + b >= 0) { /* never terminates for non-negative sums */
        a = a | 0;
    }
    printf("%d\n", a + b);
    return 0;
}
