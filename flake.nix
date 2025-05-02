{
  description = "Text-to-Speech server";

  inputs = {
    flake-parts.url = "github:hercules-ci/flake-parts";
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    nixdot.url = "github:oza6ut0ne/nixdot";
  };

  outputs =
    inputs@{ ... }:
    inputs.flake-parts.lib.mkFlake { inherit inputs; } {
      imports = [ ];
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      perSystem =
        {
          config,
          self',
          inputs',
          lib,
          pkgs,
          system,
          ...
        }:
        let
          pkgsNixdot = inputs.nixdot.packages.${system};

          src = lib.sourceByRegex ./. [
            "^.*\.py"
            "^.*\.lock"
            "^.*\.csv"
          ];

          htsvoice-tohoku-f01 = pkgs.fetchFromGitHub {
            owner = "icn-lab";
            repo = "htsvoice-tohoku-f01";
            rev = "8e3306021db135c265f5eda5f062dc489707ddf8";
            hash = "sha256-NNJG+koqGD2LxPHp8iSGDCpn7exrD91ORxunZ4b7HOg=";
          };
          voicevox-models =
            lib.sourceByRegex
              (pkgs.fetchzip {
                url = "https://github.com/VOICEVOX/voicevox_vvm/archive/refs/tags/0.1.0.zip";
                hash = "sha256-qdMyckDhM8Fa16x9Pdd1UXmZFKiSer/tZ7Mbc7RU7/M=";
              })
              [
                "^vvms$"
                "^vvms/.+\.vvm"
              ];
          voicevox-core-cpu =
            lib.sourceByRegex
              (pkgs.fetchzip (
                if system == "x86_64-linux" then
                  {
                    url = "https://github.com/VOICEVOX/onnxruntime-builder/releases/download/voicevox_onnxruntime-1.17.3/voicevox_onnxruntime-linux-x64-1.17.3.tgz";
                    hash = "sha256-bJNLc2fM7KnTNqayvi4VCoDvUlKe7Ipnvi2C0EjRc8A=";
                  }
                else
                  {
                    url = "https://github.com/VOICEVOX/onnxruntime-builder/releases/download/voicevox_onnxruntime-1.17.3/voicevox_onnxruntime-linux-arm64-1.17.3.tgz";
                    hash = "sha256-TkfArDPv+jNZk71/t0mRv13p6ZWUrjpZutvfweEBjl4=";
                  }
              ))
              [
                "^lib$"
                "^lib/libvoicevox_onnxruntime\.so.*"
              ];
          voicevox-core-cuda =
            lib.sourceByRegex
              (pkgs.fetchzip {
                url = "https://github.com/VOICEVOX/onnxruntime-builder/releases/download/voicevox_onnxruntime-1.17.3/voicevox_onnxruntime-linux-x64-cuda-1.17.3.tgz";
                hash = "sha256-HvQHvhaxgEoXtl4rTUv2tzR4wmA9Z1pIgccnvv5jEdA=";
              })
              [
                "^lib$"
                "^lib/libvoicevox_onnxruntime.*"
              ];
          voicevox-cuda-additional-libraries = pkgs.fetchzip {
            url = "https://github.com/VOICEVOX/voicevox_additional_libraries/releases/download/0.2.0/CUDA-linux-x64.zip";
            hash = "sha256-wwKJFV/aVMIyucsmp+AMaOKorcJSSpNXTEKnN8NVW5Q=";
          };

          buildInputsBase = [
            src
            pkgs.python313
            pkgs.uv
            pkgs.alsa-utils
            pkgsNixdot.open-jtalk
          ];
          buildInputsForJsay = buildInputsBase ++ [
            htsvoice-tohoku-f01
          ];
          buildInputsForVsay = buildInputsBase ++ [
            pkgs.stdenv.cc.cc
            voicevox-models
            voicevox-core-cpu
          ];
          buildInputsForVsayCuda = lib.remove pkgs.python313 buildInputsBase ++ [
            voicevox-models
            voicevox-core-cuda
            voicevox-cuda-additional-libraries
          ];
          buildInputsDevShellExcludes = [
            src
            voicevox-models
            voicevox-core-cpu
            voicevox-core-cuda
            voicevox-cuda-additional-libraries
          ];

          OPEN_JTALK_DIC = "${pkgsNixdot.open-jtalk}/dic";
          HTSVOICE = "${htsvoice-tohoku-f01.out}/tohoku-f01-angry.htsvoice";
          VOICEVOX_MODELS= "${voicevox-models}/vvms";
          ONNXRUNTIME_CPU= "${voicevox-core-cpu}/lib/libvoicevox_onnxruntime.so";
          ONNXRUNTIME_CUDA= "${voicevox-core-cuda}/lib/libvoicevox_onnxruntime.so";
          LD_LIBRARY_PATH_FOR_VSAY = pkgs.lib.makeLibraryPath buildInputsForVsay;
          LD_LIBRARY_PATH_FOR_VSAY_CUDA =
            pkgs.lib.makeLibraryPath buildInputsForVsayCuda
            + ":${voicevox-core-cuda}:${voicevox-cuda-additional-libraries}";

          makeSubCommands = packages: ''
            (${(lib.concatMapStrings (package: "\"${package.name}\" ") packages) + "\"setup\""})
          '';
          makeSubFunction = package: ''
            ${package.name}() {
              ${package}/bin/${package.name} "$@"
            }
          '';
          makeSetupFunction = packages: ''
            setup() {
              echo Downloading dependencies...
            ${(lib.concatMapStringsSep "\n" (package: "  ${package}/bin/${package.name}") packages)}
              echo Done!
            }
          '';
          makeLauncherScript = runtimeInputs: setupInputs: ''
            COMMANDS=${(makeSubCommands runtimeInputs)}
            progname=$(basename "''${ARGV0-$0}")

            is_valid_command() {
              printf '%s\n' "''${COMMANDS[@]}" | grep -qx "$1" 2>/dev/null
            }

            ${(makeSetupFunction setupInputs)}
            ${(lib.concatMapStrings makeSubFunction runtimeInputs)}

            if is_valid_command "$progname"; then
              "$progname" "$@"
            else
              subcmd="''${1-}"
              if shift && is_valid_command "$subcmd"; then
                "$subcmd" "$@"
              else
                echo "Usage: $progname {''${COMMANDS[*]}} [args...]"
                exit 1
              fi
            fi
          '';
        in
        {
          packages = rec {
            jsay = pkgs.writeShellApplication {
              name = "jsay";
              runtimeInputs = buildInputsForJsay;
              text = ''
                export HTSVOICE=''${HTSVOICE:-${HTSVOICE}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                uv run -p ${pkgs.python313} -s ${src}/jsay.py "$@"
              '';
            };
            jserver = pkgs.writeShellApplication {
              name = "jserver";
              runtimeInputs = buildInputsForJsay;
              text = ''
                export HTSVOICE=''${HTSVOICE:-${HTSVOICE}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                uv run -p ${pkgs.python313} -s ${src}/jserver.py "$@"
              '';
            };
            vsay = pkgs.writeShellApplication {
              name = "vsay";
              runtimeInputs = buildInputsForVsay;
              text = ''
                export ONNXRUNTIME=''${ONNXRUNTIME:-${ONNXRUNTIME_CPU}}
                export VOICEVOX_MODELS=''${VOICEVOX_MODELS:-${VOICEVOX_MODELS}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY}
                uv run -p ${pkgs.python313} -s ${src}/vsay.py "$@"
              '';
            };
            vserver = pkgs.writeShellApplication {
              name = "vserver";
              runtimeInputs = buildInputsForVsay;
              text = ''
                export ONNXRUNTIME=''${ONNXRUNTIME:-${ONNXRUNTIME_CPU}}
                export VOICEVOX_MODELS=''${VOICEVOX_MODELS:-${VOICEVOX_MODELS}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY}
                uv run -p ${pkgs.python313} -s ${src}/vserver.py "$@"
              '';
            };
            vsay-cuda = pkgs.writeShellApplication {
              name = "vsay";
              runtimeInputs = buildInputsForVsayCuda;
              text = ''
                export ONNXRUNTIME=''${ONNXRUNTIME:-${ONNXRUNTIME_CUDA}}
                export VOICEVOX_MODELS=''${VOICEVOX_MODELS:-${VOICEVOX_MODELS}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY_CUDA}
                uv run -p 3.13 -s ${src}/vsay.py "$@"
              '';
            };
            vserver-cuda = pkgs.writeShellApplication {
              name = "vserver";
              runtimeInputs = buildInputsForVsayCuda;
              text = ''
                export ONNXRUNTIME=''${ONNXRUNTIME:-${ONNXRUNTIME_CUDA}}
                export VOICEVOX_MODELS=''${VOICEVOX_MODELS:-${VOICEVOX_MODELS}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY_CUDA}
                uv run -p 3.13 -s ${src}/vserver.py "$@"
              '';
            };
            jtts = pkgs.writeShellApplication rec {
              name = "jtts";
              runtimeInputs = [
                jsay
                jserver
              ];
              text = (
                makeLauncherScript runtimeInputs [
                  jsay
                ]
              );
            };
            tts = pkgs.writeShellApplication rec {
              name = "tts";
              runtimeInputs = [
                jsay
                jserver
                vsay
                vserver
              ];
              text = (
                makeLauncherScript runtimeInputs [
                  jsay
                  vsay
                ]
              );
            };
            tts-cuda = pkgs.writeShellApplication rec {
              name = "tts";
              runtimeInputs = [
                jsay
                jserver
                vsay-cuda
                vserver-cuda
              ];
              text = (
                makeLauncherScript runtimeInputs [
                  jsay
                  vsay-cuda
                ]
              );
            };
          };

          devShells = rec {
            default = jsay;
            jsay = pkgs.mkShell {
              name = "jsay";
              inherit OPEN_JTALK_DIC HTSVOICE;
              buildInputs = lib.subtractLists buildInputsDevShellExcludes buildInputsForJsay;
            };
            vsay = pkgs.mkShell {
              name = "vsay";
              inherit OPEN_JTALK_DIC VOICEVOX_MODELS;
              ONNXRUNTIME = ONNXRUNTIME_CPU;
              LD_LIBRARY_PATH = LD_LIBRARY_PATH_FOR_VSAY;
              buildInputs = lib.subtractLists buildInputsDevShellExcludes buildInputsForVsay;
            };
            vsay-cuda = pkgs.mkShell {
              name = "vsay-cuda";
              inherit OPEN_JTALK_DIC VOICEVOX_MODELS;
              ONNXRUNTIME = ONNXRUNTIME_CUDA;
              LD_LIBRARY_PATH = LD_LIBRARY_PATH_FOR_VSAY_CUDA;
              buildInputs = lib.subtractLists buildInputsDevShellExcludes buildInputsForVsayCuda;
            };
          };
        };
      flake = { };
    };
}
