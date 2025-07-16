{
  description = "Text-to-Speech server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    nixpkgs-23_11.url = "github:NixOS/nixpkgs/release-23.11";

    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    nix-appimage = {
      url = "github:ralismark/nix-appimage";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nixdot = {
      url = "github:oza6ut0ne/nixdot";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-parts.follows = "flake-parts";
    };
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
          system,
          ...
        }:
        let
          pkgs = import inputs.nixpkgs {
            inherit system;
            config.allowUnfree = true;
          };
          pkgs-23_11 = import inputs.nixpkgs-23_11 {
            inherit system;
            config.allowUnfree = true;
          };
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
          voicevox-core-cpu = lib.sourceByRegex pkgs.voicevox-core [
            "^lib$"
            "^lib/libonnxruntime\.so\.1\.13\.1$"
          ];
          voicevox-core-cuda = lib.sourceByRegex (pkgs.fetchzip {
            url = "https://github.com/VOICEVOX/voicevox_core/releases/download/0.15.7/voicevox_core-linux-x64-gpu-0.15.7.zip";
            hash = "sha256-M8ZESvVpK8BWUoSgoPn5/z9vaufP0+LH0vldH7Wg1Zk=";
          }) [ "^libonnxruntime.*\.so.*" ];
          voicevox-cuda-additional-libraries = pkgs.fetchzip {
            url = "https://github.com/VOICEVOX/voicevox_additional_libraries/releases/download/0.1.0/CUDA-linux-x64.zip";
            hash = "sha256-iXUN7MQXI/DPwQQH5jTUQR1n8ry0gEHWxvCo8xufXdk=";
          };

          buildInputsBase = [
            src
            pkgs.python313
            pkgs.uv
            pkgs.pulseaudio
            pkgsNixdot.open-jtalk
          ];
          buildInputsForJsay = buildInputsBase ++ [
            htsvoice-tohoku-f01
          ];
          buildInputsForVsay = buildInputsBase ++ [
            pkgs.stdenv.cc.cc
            voicevox-core-cpu
          ];
          buildInputsForVsayCuda = lib.remove pkgs.python313 buildInputsBase ++ [
            pkgs-23_11.cudaPackages_11.cuda_cudart
            voicevox-core-cuda
            voicevox-cuda-additional-libraries
          ];
          buildInputsDevShellExcludes = [
            src
            voicevox-core-cpu
            voicevox-core-cuda
            voicevox-cuda-additional-libraries
          ];

          OPEN_JTALK_DIC = "${pkgsNixdot.open-jtalk}/dic";
          HTSVOICE = "${htsvoice-tohoku-f01.out}/tohoku-f01-angry.htsvoice";
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
              export ONLY_SYNC=1
            ${(lib.concatMapStringsSep "\n" (package: "  ${package}/bin/${package.name}") packages)}
              echo Done!
            }
          '';
          makeLauncherScript = runtimeInputs: ''
            COMMANDS=${(makeSubCommands runtimeInputs)}
            progname=$(basename "''${ARGV0-$0}")

            is_valid_command() {
              printf '%s\n' "''${COMMANDS[@]}" | grep -qx "$1" 2>/dev/null
            }

            ${(makeSetupFunction runtimeInputs)}
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
            default = tts;
            cuda = tts-cuda;

            jsay = pkgs.writeShellApplication {
              name = "jsay";
              runtimeInputs = buildInputsForJsay;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p ${pkgs.python313} --script ${src}/jsay.py
                  exit
                fi
                export HTSVOICE=''${HTSVOICE:-${HTSVOICE}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                uv run -p ${pkgs.python313} -s ${src}/jsay.py "$@"
              '';
            };

            jserver = pkgs.writeShellApplication {
              name = "jserver";
              runtimeInputs = buildInputsForJsay;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p ${pkgs.python313} --script ${src}/jserver.py
                  exit
                fi
                export HTSVOICE=''${HTSVOICE:-${HTSVOICE}}
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                uv run -p ${pkgs.python313} -s ${src}/jserver.py "$@"
              '';
            };

            vsay = pkgs.writeShellApplication {
              name = "vsay";
              runtimeInputs = buildInputsForVsay;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p ${pkgs.python313} --script ${src}/vsay.py
                  exit
                fi
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY}
                uv run -p ${pkgs.python313} -s ${src}/vsay.py "$@"
              '';
            };

            vserver = pkgs.writeShellApplication {
              name = "vserver";
              runtimeInputs = buildInputsForVsay;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p ${pkgs.python313} --script ${src}/vserver.py
                  exit
                fi
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY}
                uv run -p ${pkgs.python313} -s ${src}/vserver.py "$@"
              '';
            };

            vsay-cuda = pkgs.writeShellApplication {
              name = "vsay";
              runtimeInputs = buildInputsForVsayCuda;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p 3.13.2 --script ${src}/vsay.py
                  exit
                fi
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY_CUDA}
                uv run -p 3.13.2 -s ${src}/vsay.py "$@"
              '';
            };

            vserver-cuda = pkgs.writeShellApplication {
              name = "vserver";
              runtimeInputs = buildInputsForVsayCuda;
              text = ''
                if [ -n "''${ONLY_SYNC-}" ]; then
                  uv sync -p 3.13.2 --script ${src}/vserver.py
                  exit
                fi
                export OPEN_JTALK_DIC=''${OPEN_JTALK_DIC:-${OPEN_JTALK_DIC}}
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${LD_LIBRARY_PATH_FOR_VSAY_CUDA}
                uv run -p 3.13.2 -s ${src}/vserver.py "$@"
              '';
            };

            jtts = pkgs.writeShellApplication rec {
              name = "jtts";
              runtimeInputs = [
                jsay
                jserver
              ];
              text = (makeLauncherScript runtimeInputs);
              derivationArgs = {
                postCheck = ''
                  ${
                    (lib.concatMapStringsSep "\n" (
                      package: "ln -s ${package}/bin/${package.name} $out/bin/"
                    ) runtimeInputs)
                  }
                '';
              };
            };

            tts = pkgs.writeShellApplication rec {
              name = "tts";
              runtimeInputs = [
                jsay
                jserver
                vsay
                vserver
              ];
              text = (makeLauncherScript runtimeInputs);
              derivationArgs = {
                postCheck = ''
                  ${
                    (lib.concatMapStringsSep "\n" (
                      package: "ln -s ${package}/bin/${package.name} $out/bin/"
                    ) runtimeInputs)
                  }
                '';
              };
            };

            tts-cuda = pkgs.writeShellApplication rec {
              name = "tts";
              runtimeInputs = [
                jsay
                jserver
                vsay-cuda
                vserver-cuda
              ];
              text = (makeLauncherScript runtimeInputs);
              derivationArgs = {
                postCheck = ''
                  ${
                    (lib.concatMapStringsSep "\n" (
                      package: "ln -s ${package}/bin/${package.name} $out/bin/"
                    ) runtimeInputs)
                  }
                '';
              };
            };

            jtts-appimage = inputs.nix-appimage.lib.${system}.mkAppImage {
              program = "${jtts.out}/bin/jtts";
            };

            tts-appimage = inputs.nix-appimage.lib.${system}.mkAppImage {
              program = "${tts.out}/bin/tts";
            };

            tts-cuda-appimage = inputs.nix-appimage.lib.${system}.mkAppImage {
              program = "${tts-cuda.out}/bin/tts";
              pname = "tts-cuda";
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
              inherit OPEN_JTALK_DIC;
              LD_LIBRARY_PATH = LD_LIBRARY_PATH_FOR_VSAY;
              buildInputs = lib.subtractLists buildInputsDevShellExcludes buildInputsForVsay;
            };
            vsay-cuda = pkgs.mkShell {
              name = "vsay-cuda";
              inherit OPEN_JTALK_DIC;
              LD_LIBRARY_PATH = LD_LIBRARY_PATH_FOR_VSAY_CUDA;
              buildInputs = lib.subtractLists buildInputsDevShellExcludes buildInputsForVsayCuda;
            };
          };
        };
      flake = { };
    };
}
