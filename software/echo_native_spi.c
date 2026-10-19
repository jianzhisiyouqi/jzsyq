/* Echo LCD-only SPI batching accelerator. AMD PG153 legacy Standard SPI.
 * No GPIO, servo, physical-address mapping, clock changes, or CS writes.
 * Python holds CS across <=4096-byte calls and handles LCD-only aborts.
 * Completion is IPISR bit2 (wire/pipeline complete), never SR.TX_EMPTY alone.
 * Build with ECHO_SPI_SELFTEST for a RAM-only behavioral test executable.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { IPISR=0x20, CR=0x60, SR=0x64, DTR=0x68, DRR=0x6c,
       DONE=4, ERRORS=0x3e23, INHIBIT=0x100, MAX_BYTES=4096 };

#ifdef ECHO_SPI_SELFTEST
static struct {
    uint32_t control, events;
    uint8_t pending[16], wire[MAX_BYTES];
    size_t pending_n, rx_n, wire_n, max_fifo;
    unsigned polls;
    int active, hang, drop_rx, inject_error, violation;
    double now;
} model;

static double mono_now(void) { model.now+=0.0001; return model.now; }
static uint32_t reg_read(volatile uint32_t *base, unsigned offset) {
    (void)base;
    if (offset==CR) return model.control;
    /* Deliberately report TX_EMPTY before actual wire completion. */
    if (offset==SR) return 0x24u | (model.rx_n ? 0u : 1u);
    if (offset==DRR) {
        if (!model.rx_n) model.violation=1;
        else --model.rx_n;
        return 0;
    }
    if (offset!=IPISR) { model.violation=1; return 0; }
    if (model.active) {
        if (model.inject_error) model.events|=1u;
        if (++model.polls>=3 && !model.hang && !model.inject_error) {
            if (model.wire_n+model.pending_n>MAX_BYTES) model.violation=1;
            else memcpy(model.wire+model.wire_n,model.pending,model.pending_n);
            model.wire_n+=model.pending_n;
            model.rx_n=model.drop_rx ? 0 : model.pending_n;
            model.pending_n=0;
            model.active=0;
            model.events|=DONE;
            if (model.rx_n==16) model.events|=0x10;
        }
    }
    return model.events;
}
static void reg_write(volatile uint32_t *base, unsigned offset, uint32_t value) {
    (void)base;
    if (offset==CR) {
        if (model.active && (value&INHIBIT)) model.violation=1;
        model.control=value;
        if (!(value&INHIBIT) && model.pending_n) {
            if (model.events&DONE) model.violation=1;
            model.active=1;
            model.polls=0;
        }
    } else if (offset==DTR) {
        if (!(model.control&INHIBIT) || model.pending_n>=16) model.violation=1;
        else model.pending[model.pending_n++]=(uint8_t)value;
        if (model.pending_n>model.max_fifo) model.max_fifo=model.pending_n;
    } else if (offset==IPISR) {
        if (value&~model.events) model.violation=1;
        model.events^=value; /* Toggle-on-write, not write-one-to-clear. */
    } else model.violation=1;
}
#else
static void io_barrier(void) {
#if defined(__arm__) || defined(__aarch64__)
    __asm__ __volatile__("dmb sy" ::: "memory");
#else
    __sync_synchronize();
#endif
}
static uint32_t reg_read(volatile uint32_t *base, unsigned offset) {
    uint32_t value=base[offset/4];
    io_barrier();
    return value;
}
static void reg_write(volatile uint32_t *base, unsigned offset, uint32_t value) {
    io_barrier();
    base[offset/4]=value;
    io_barrier();
}
static double mono_now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC,&ts)!=0) return -1.0;
    return (double)ts.tv_sec+(double)ts.tv_nsec/1000000000.0;
}
#endif

static int expired(double deadline) {
    double now=mono_now();
    return now<0 || now>=deadline;
}
static int result(volatile uint32_t *base, uint32_t *report,
                  size_t completed, int code) {
    report[0]=reg_read(base,SR);
    report[1]=reg_read(base,IPISR);
    report[2]=reg_read(base,CR);
    report[3]=(uint32_t)completed;
    return code;
}
uint32_t echo_spi_abi(void) { return 0x00010001u; }

int echo_spi_send(volatile uint32_t *base, const uint8_t *data, size_t length,
                  uint32_t cr_run, uint32_t cr_idle, double batch_timeout,
                  double chunk_timeout, uint32_t *report) {
    double started, chunk_deadline;
    size_t completed=0;
    if (!base || !data || !report || !length || length>MAX_BYTES
        || !isfinite(batch_timeout) || batch_timeout<=0
        || !isfinite(chunk_timeout) || chunk_timeout<=0 || chunk_timeout>2.0
        || cr_idle!=(cr_run|INHIBIT) || (cr_run&INHIBIT)) return -1;
    memset(report,0,4*sizeof(uint32_t));
    started=mono_now();
    if (started<0) return -6;
    chunk_deadline=started+chunk_timeout;
    if ((reg_read(base,CR)&0x3ffu)!=cr_idle)
        return result(base,report,completed,-5);
    while (completed<length) {
        uint32_t events, status;
        size_t batch=length-completed, received=0, i;
        double deadline, now;
        if (batch>16) batch=16;
        if (expired(chunk_deadline)) return result(base,report,completed,-7);
        events=reg_read(base,IPISR);
        if (events&ERRORS) return result(base,report,completed,-2);
        if (events) reg_write(base,IPISR,events); /* Clear ONLY observed bits. */
        status=reg_read(base,SR);
        if (status&0x10) return result(base,report,completed,-2);
        if ((status&5u)!=5u) return result(base,report,completed,-5);
        for (i=0;i<batch;++i) reg_write(base,DTR,data[completed+i]);
        now=mono_now();
        if (now<0) return result(base,report,completed,-6);
        deadline=now+batch_timeout;
        if (deadline>chunk_deadline) deadline=chunk_deadline;
        reg_write(base,CR,cr_run);
        for (;;) {
            events=reg_read(base,IPISR);
            if (events&ERRORS) return result(base,report,completed,-2);
            if (events&DONE) break;
            if (expired(deadline)) return result(base,report,completed,-3);
        }
        /* Final byte has left the pipeline. It is now safe to inhibit SCK. */
        reg_write(base,CR,cr_idle);
        while (received<batch) {
            status=reg_read(base,SR);
            if (status&0x10) return result(base,report,completed,-2);
            if (!(status&1u)) { (void)reg_read(base,DRR); ++received; }
            else if (expired(deadline)) return result(base,report,completed,-4);
        }
        if (reg_read(base,IPISR)&ERRORS) return result(base,report,completed,-2);
        completed+=batch;
    }
    return result(base,report,completed,0);
}

#ifdef ECHO_SPI_SELFTEST
static void reset_model(void) {
    memset(&model,0,sizeof(model));
    model.control=0x19e;
}
static void check(int truth, const char *message) {
    if (!truth) { fprintf(stderr,"native SPI self-test FAILED: %s\n",message); exit(1); }
}
int main(void) {
    uint32_t dummy[32]={0}, report[4];
    uint8_t data[MAX_BYTES];
    size_t sizes[]={1,15,16,17,31,32,33,MAX_BYTES},i,j;
    int code;
    for (i=0;i<MAX_BYTES;++i) data[i]=(uint8_t)(i*37u);
    for (i=0;i<sizeof(sizes)/sizeof(sizes[0]);++i) {
        reset_model();
        model.events=0x14; /* Pending completion + RX-full must be TOW cleared. */
        code=echo_spi_send(dummy,data,sizes[i],0x9e,0x19e,.02,2.0,report);
        check(code==0,"normal transfer");
        check(model.wire_n==sizes[i] && !memcmp(model.wire,data,sizes[i]),"byte order/length");
        check(report[3]==sizes[i],"completed count");
        check(model.rx_n==0 && model.pending_n==0 && !model.active,"FIFO drain");
        check(model.max_fifo<=16 && model.control==0x19e,"FIFO size/idle control");
        check(!model.violation,"early inhibit, unobserved TOW, or invalid register write");
    }
    reset_model(); model.hang=1;
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.02,report);
    check(code==-3 && model.active && !model.violation,"completion timeout, not TX_EMPTY");
    reset_model(); model.drop_rx=1;
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.02,report);
    check(code==-4 && !model.violation,"receive drain timeout");
    reset_model(); model.inject_error=1;
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.02,report);
    check(code==-2 && !model.violation,"hardware interrupt error");
    reset_model(); model.rx_n=1;
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.02,report);
    check(code==-5 && !model.pending_n,"dirty FIFO rejected");
    reset_model(); model.control=0x9e;
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.02,report);
    check(code==-5 && !model.pending_n,"unexpected control rejected");
    reset_model();
    code=echo_spi_send(dummy,data,17,0x9e,0x19e,.002,.00001,report);
    check(code==-7 && !model.violation,"chunk deadline");
    check(echo_spi_send(dummy,data,0,0x9e,0x19e,.02,2,report)==-1,"zero length rejected");
    check(echo_spi_send(dummy,data,MAX_BYTES+1,0x9e,0x19e,.02,2,report)==-1,"oversize rejected");
    for (j=0;j<4;++j) report[j]=0;
    puts("native SPI self-test passed (16 cases; RAM model only)");
    return 0;
}
#endif
