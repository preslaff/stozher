// The cloud public key STOZHER boards trust for signed acknowledgements (docs/control.md), shared by the witness module
// and the gateway store. Default: the public half of the TEST cloud key (tests/vectors/ack_messages.json). A kit signs
// with its own seed (infra/kit/init.sh): build with -D STOZHER_CLOUD_PUBLIC_HEX=<64 hex digits, no quotes> taken from
// that kit's GET /v1/control/public-key, or its acks are ignored.
#pragma once

#define STOZHER_KEY_STR2(x) #x
#define STOZHER_KEY_STR(x) STOZHER_KEY_STR2(x)
#ifdef STOZHER_CLOUD_PUBLIC_HEX
static const char kStozherCloudPublicHex[] = STOZHER_KEY_STR(STOZHER_CLOUD_PUBLIC_HEX);
#else
static const char kStozherCloudPublicHex[] = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8";
#endif
static const unsigned char kStozherCloudKeyId = 1;
