{
  description = "ggwave server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-appimage = {
      url = "github:ralismark/nix-appimage";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{ self, ... }:
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
          workspace = inputs.uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

          overlay = workspace.mkPyprojectOverlay {
            sourcePreference = "wheel";
          };

          pyprojectOverrides = _final: _prev: {
            ggwave = _prev.ggwave.overrideAttrs (old: {
              buildInputs = (old.buildInputs or [ ]) ++ _final.resolveBuildSystem ({ setuptools = [ ]; });
            });

            soundcard = _prev.soundcard.overrideAttrs (old: {
              buildInputs = (old.buildInputs or [ ]) ++ _final.resolveBuildSystem ({ }) ++ [ pkgs.pulseaudio ];
            });
          };

          pkgs = import inputs.nixpkgs {
            inherit system;
          };

          python = pkgs.python310;

          pythonSet =
            (pkgs.callPackage inputs.pyproject-nix.build.packages {
              inherit python;
            }).overrideScope
              (
                lib.composeManyExtensions [
                  inputs.pyproject-build-systems.overlays.default
                  overlay
                  pyprojectOverrides
                ]
              );

          buildInputs = [
            pkgs.libsndfile
            pkgs.pulseaudio
          ];

          ldLibraryPath = lib.makeLibraryPath buildInputs;
        in
        {
          packages = {
            default = self'.packages.ggwave;

            venv = pythonSet.mkVirtualEnv "gsay-env" workspace.deps.default;

            gsay = pkgs.writeShellApplication {
              name = "gsay";
              runtimeInputs = buildInputs;
              text = ''
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
                ${self'.packages.venv}/bin/gsay "$@"
              '';
            };

            gserver = pkgs.writeShellApplication {
              name = "gserver";
              runtimeInputs = buildInputs;
              text = ''
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
                ${self'.packages.venv}/bin/gserver "$@"
              '';
            };

            glisten = pkgs.writeShellApplication {
              name = "glisten";
              runtimeInputs = buildInputs;
              text = ''
                export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
                ${self'.packages.venv}/bin/glisten "$@"
              '';
            };

            ggwave = pkgs.writeShellApplication rec {
              name = "ggwave";
              runtimeInputs = with self'.packages; [
                gsay
                gserver
                glisten
              ];
              text = ''
                COMMANDS=("gsay" "gserver" "glisten")
                progname=$(basename "''${ARGV0-$0}")

                is_valid_command() {
                  printf '%s\n' "''${COMMANDS[@]}" | grep -qx "$1" 2>/dev/null
                }

                gsay() {
                  ${self'.packages.gsay}/bin/gsay "$@"
                }

                gserver() {
                  ${self'.packages.gserver}/bin/gserver "$@"
                }

                glisten() {
                  ${self'.packages.glisten}/bin/glisten "$@"
                }

                if is_valid_command "$progname"; then
                  "$progname" "$@"
                else
                  subcmd="''${1-}"
                  if shift && is_valid_command "$subcmd"; then
                    "$subcmd" "$@"
                  else
                    echo "Usage: $progname { ''${COMMANDS[*]} } [args...]"
                    exit 1
                  fi
                fi
              '';
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

            appimage = inputs.nix-appimage.lib.${system}.mkAppImage {
              program = "${self'.packages.ggwave}/bin/ggwave";
            };
          };

          devShells = {
            default =
              let
                editableOverlay = workspace.mkEditablePyprojectOverlay {
                  root = "$REPO_ROOT";
                };

                editablePythonSet = pythonSet.overrideScope (
                  lib.composeManyExtensions [
                    editableOverlay
                    (final: prev: {
                      gsay = prev.gsay.overrideAttrs (old: {
                        src = lib.fileset.toSource {
                          root = old.src;
                          fileset = lib.fileset.unions [
                            (old.src + "/pyproject.toml")
                            # (old.src + "/README.md")
                            (old.src + "/src/gsay/__init__.py")
                          ];
                        };

                        nativeBuildInputs =
                          old.nativeBuildInputs
                          ++ final.resolveBuildSystem {
                            editables = [ ];
                          };
                      });
                    })
                  ]
                );

                virtualenv = editablePythonSet.mkVirtualEnv "gsay-dev-env" workspace.deps.all;
              in
              pkgs.mkShell {
                packages = [
                  virtualenv
                  pkgs.uv
                ];

                env = {
                  UV_NO_SYNC = "1";
                  UV_PYTHON = python.interpreter;
                  UV_PYTHON_DOWNLOADS = "never";
                  LD_LIBRARY_PATH = ldLibraryPath;
                };

                shellHook = ''
                  # Undo dependency propagation by nixpkgs.
                  unset PYTHONPATH

                  # Get repository root using git. This is expanded at runtime by the editable `.pth` machinery.
                  export REPO_ROOT=$(git rev-parse --show-toplevel)
                '';
              };
          };
        };
    };
}
