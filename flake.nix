{
  description = "ggwave server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

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
  };

  outputs =
    {
      self,
      nixpkgs,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

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

      pkgs = nixpkgs.legacyPackages.x86_64-linux;

      python = pkgs.python310;

      pythonSet =
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
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
      packages.x86_64-linux = {
        default = pythonSet.mkVirtualEnv "gsay" workspace.deps.default;

        gsay = pkgs.writeShellApplication {
          name = "gsay";
          runtimeInputs = buildInputs;
          text = ''
            export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
            ${self.packages.x86_64-linux.default}/bin/gsay "$@"
          '';
        };

        gserver = pkgs.writeShellApplication {
          name = "gsay";
          runtimeInputs = buildInputs;
          text = ''
            export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
            ${self.packages.x86_64-linux.default}/bin/gserver "$@"
          '';
        };

        glisten = pkgs.writeShellApplication {
          name = "gsay";
          runtimeInputs = buildInputs;
          text = ''
            export LD_LIBRARY_PATH=''${LD_LIBRARY_PATH-}:${ldLibraryPath}
            ${self.packages.x86_64-linux.default}/bin/glisten "$@"
          '';
        };
      };

      devShells.x86_64-linux = {
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

            virtualenv = editablePythonSet.mkVirtualEnv "gsay-dev" workspace.deps.all;
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
}
