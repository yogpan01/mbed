# mbed OS 5.0 EPR - 16.07 Early Partner Release  

This is the release note for the 16.07 Early Partner Release (EPR) of mbed OS and associated tools. It summarises the major changes in this version of mbed OS, as well as the steps you need to take to support this release on your lead platforms. 

## About this release 

mbed OS 5.0 marks significant changes and enhancements that have accelerated important aspects of our roadmap, opened up the applicability of mbed OS to many more use cases and developers, and unlocked compatibility with our previous mbed OS 2.0 ("Classic") ecosystem.


The Early Partner Release (EPR) gives you early visibility of repositories and documents for the upcoming release at the final stages of development. This should allow you to update your ports, so that your platforms support mbed OS by the time it is publicly released.


## Version summary

The headline changes in this release are:
 
* **RTOS** - mbed OS now incorporates an RTOS. This much-requested feature provides native thread support to the OS and applications, simplifying development and integration of complex and robust application components like networking stacks. It also enables both blocking and non-blocking programming design patterns. RTOS requires very limited system overhead, opening up mbed OS to a much wider range of applications.

* **Tooling** - we have simplified the tooling and introduced native support for building and testing across the ARM Compiler 5, ARM GCC Embedded and IAR compiler toolchains. A command line interface script (mbed CLI) now drives the established mbed 2.0 build system to build the OS and associated developer applications and components. Dependencies are explicitly pinned to provide full reproducibility of builds. The target and toolchain can be selected independently of each other, and we run CI on mbed OS across all these compiler toolchains on every commit. yotta is not used in this release.

* **Compatibility** - the introduction of the RTOS and changes to tooling have opened up the technical possibility of compatibility with the mbed OS 2.0 ("Classic") ecosystem. We have taken this opportunity to re-base and merge the two development lines so that we now have just one platform, with backward compatibility. Existing partners can take advantage of investments in mbed made over the years, and both new and existing partners need to invest in only one project. Developers can benefit from all legacy components and libraries, alongside the existing and new features of mbed OS. 

These architectural changes enable merging the mbed OS 2.0 and mbed OS 3.0 codebases, websites and ecosystems, and will be marked with a major revision update - mbed OS 5.0.

Based on these changes and the hard work of our partners, mbed OS target support is also accelerated. This release supports multiple target platforms from multiple partners, with more ports regularly made available on a newly introduced minor release tick every two weeks.

The following sections provide more details of these and other changes in this release.

## Core

### RTOS


mbed OS now incorporates an RTOS.

The RTOS core is based on the widely used open-source CMSIS-RTOS RTX, providing an established kernel that can support threads and other RTOS services to very tiny devices. The RTOS primitives are always available, so that drivers and applications can rely on features such as threads, semaphores and mutexes. The RTOS is initialised ahead of entering the `main()` thread, making it transparent and easy to write composable components. Many applications may choose not to create any threads beyond the main thread, yet code can still take advantage of automatically entering the RTOS idle state on calls that block. 

The implementation is based on CMSIS-RTOS RTX 4.79.0, and we will be tracking and contributing to the development of CMSIS-RTOS releases, allowing us to pick up support for new versions and architectural features such as TrustZone for Cortex-M.


The MINAR event-based scheduler is not included in this release. An early version of a simplified event scheduler library that can be used within the RTOS is available. It enables the same design pattern by providing event loops within threads. This library will soon be managed as part of the core OS codebase.

### Drivers and support libraries

There is now driver support for a wide range of standard MCU peripherals:


* DigitalIn, DigitalOut, DigitalInOut
* InterruptIn
* PortIn, PortOut, PortInOut
* BusIn, BusOut, BusInOut
* AnalogIn, AnalogOut
* PwmOut
* I2C, I2CSlave
* SPI, SPISlave
* Serial

These drivers have been internally upgraded to integrate thread safety logic, whilst maintaining compatibility with the mbed OS 2.0 APIs. This has been done within the generic components of the drivers to avoid changes to the different HAL implementations. 

A new ``Callback`` class supersedes ``FunctionPointer`` (still supported) to provide a more flexible and neater syntax for capturing and calling static function and class member callbacks. It now uses the same class regardless of the number of arguments on the callback.


### C libraries

The C libraries provided with each of the supported toolchains have been integrated into mbed OS, including implementation of thread safety support.  

## Security

### uVisor 

uVisor has been upgraded to support the RTOS. 


We have made the modifications CMSIS required to allow uVisor to hook interrupts. These will be upstreamed to future CMSIS releases.

uVisor now includes a disabled mode for ARMv7-M and ARMv6-M architectures that maintains code compatibility even when uVisor is not present or active, enabling a smooth software upgrade path as platforms introduce support for uVisor.

### Crypto

The crypto libraries now support an insecure NULL entropy mode to simplify support during development, a HAL API for providing strong entropy sources based on a hardware TRNG, and a reasonable strength entropy source based on non-volatile storage.

### mbed TLS


We have integrated version 2.3.0 of mbed TLS, providing TLS and DTLS support for services.


See [the mbed TLS 2.3.0 release note](https://tls.mbed.org/tech-updates/releases/mbedtls-2.3.0-2.1.5-and-1.3.17-release).


## Connectivity

The sockets API has been revised to support:


 * Multiple stacks
 * Multiple interfaces
 * Synchronous and asynchronous APIs

We have successfully applied this to Ethernet, 6LoWPAN Mesh and WiFi interfaces.

We've introduced an 802.15.4 MAC HAL to enable simplified porting of the 6LoWPAN and Thread stacks to different 802.15.4 radios. This release includes ports for multiple transceivers.

We've extended our Bluetooth Low Energy (BLE) API  to support user-defined scheduling policies. This maintains compatibility with mbed OS 2.0 BLE applications, while allowing developers to take advantage of the RTOS to design more efficient solutions.

## Services

mbed OS integrates the latest mbed Client, providing connectivity and management services from mbed Device Connector.


See [the mbed Device Connector site](https://connector.mbed.com/).

## Tools and workflow

The tools and workflows have had a significant reworking to address feedback from previous releases on the use of yotta and the desire for backward compatibility with mbed OS 2.0. In this release we now build the OS, components and applications based on the existing mbed 2.0 build scripts. This release does not use yotta; applications depend instead on an mbed OS repository where all dependencies are pinned, making applications reproducible.

You can use a new top level command line interface (mbed CLI) to drive the build, package management and test scripts. It is also a natural integration point for IDEs.


See [the mbed CLI repository](https://github.com/ARMmbed/mbed-cli).

We are moving towards a single repository for mbed OS and all its core components to simplify development and management. We are still using a number of repository dependencies, but we will integrate and eliminate each of these external dependencies at the right time.


See [the mbed OS repository](https://github.com/ARMmbed/mbed-os).

We now support building and testing across multiple toolchains (ARM Compiler 5, GCC, IAR). Supporting these three toolchains is a requirement for partner ports.

Code is compatible with c99/C++03, and doesn't use any C++11 or C++14 features.


We support generation of project files to enable opening mbed projects in Keil MDK, IAR Workbench and other environments. This is useful for development and launching debug sessions.

The build tools now emit static RAM and FLASH sizes and a top level breakdown on every build.


## mbed Enabled

The mbed Enabled program has been formalised, providing versioned compliance criteria and technical requirements for boards, on-board interfaces and end products.

The following resources are available:


 * The mbed Enabled requirements documents, available in the Partner Portal 
 * The mbed Enabled application form

## Targets

Supported partner targets will be listed for the 16.07 release, based on successful ports. See below for details on how to get your target supported.


## Getting Started


To get started with and begin porting for this release, see [EPR Getting Started](https://github.com/ARMmbed/EPR-Getting-Started).

You can download this release [here](https://github.com/ARMmbed/mbed-os/releases/tag/mbed-os-5.0.1-epr).
